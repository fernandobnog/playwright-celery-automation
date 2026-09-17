"""
Persistent SQLite repository for recording flow executions,
scraped items, and webhook dispatch logs.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.config import settings

logger = logging.getLogger(__name__)


class PipelineRepository:
    """
    Thread-safe SQLite storage for workflow logs and scraped entities.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Parse sqlite:/// url or use default data dir
            raw_url = settings.DATABASE_URL.replace("sqlite:///", "")
            self.db_path = Path(raw_url)
        else:
            self.db_path = Path(db_path)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=20.0)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode for high concurrency
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self):
        """Initializes tables if they do not exist."""
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS scraped_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    source_url TEXT,
                    quote TEXT,
                    author TEXT,
                    tags TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS flow_executions (
                    task_id TEXT PRIMARY KEY,
                    flow_name TEXT,
                    status TEXT,
                    input_payload TEXT,
                    output_payload TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS webhook_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    webhook_url TEXT,
                    status_code INTEGER,
                    response_body TEXT,
                    is_success BOOLEAN,
                    dispatched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS network_link_status (
                    link TEXT PRIMARY KEY,
                    status TEXT,
                    latency TEXT,
                    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_changed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS editorial_publications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT UNIQUE,
                    tema TEXT NOT NULL,
                    categoria TEXT NOT NULL,
                    angulo_editorial TEXT,
                    doc_id TEXT,
                    doc_url TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
            self._backfill_editorial_publications(conn)

    def save_scraped_items(self, task_id: str, source_url: str, items: List[Dict[str, Any]]):
        """Inserts a batch of scraped items."""
        with self._get_connection() as conn:
            for item in items:
                tags_str = json.dumps(item.get("tags", []))
                conn.execute(
                    """
                    INSERT INTO scraped_items (task_id, source_url, quote, author, tags)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        source_url,
                        item.get("quote"),
                        item.get("author"),
                        tags_str,
                    ),
                )
            conn.commit()
            logger.info("Saved %d items for task %s", len(items), task_id)

    def log_flow_start(self, task_id: str, flow_name: str, input_payload: Dict[str, Any]):
        """Logs the initialization of a workflow."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO flow_executions (task_id, flow_name, status, input_payload, updated_at)
                VALUES (?, ?, 'STARTED', ?, CURRENT_TIMESTAMP)
                """,
                (task_id, flow_name, json.dumps(input_payload)),
            )
            conn.commit()

    def log_flow_complete(self, task_id: str, output_payload: Dict[str, Any]):
        """Marks a workflow as completed."""
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE flow_executions
                SET status = 'SUCCESS', output_payload = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
                """,
                (json.dumps(output_payload), task_id),
            )
            conn.commit()

    def log_flow_error(self, task_id: str, error_message: str):
        """Marks a workflow as failed with error details."""
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE flow_executions
                SET status = 'FAILURE', error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
                """,
                (error_message, task_id),
            )
            conn.commit()

    def log_webhook_dispatch(
        self,
        task_id: str,
        webhook_url: str,
        status_code: int,
        response_body: str,
        is_success: bool,
    ):
        """Records webhook dispatch attempt."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO webhook_logs (task_id, webhook_url, status_code, response_body, is_success)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, webhook_url, status_code, response_body, is_success),
            )
            conn.commit()

    def get_link_statuses(self) -> Dict[str, str]:
        """Retrieves currently stored link statuses from local SQLite."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT link, status FROM network_link_status")
            return {row["link"]: row["status"] for row in cursor.fetchall()}

    def update_link_status(self, link: str, status: str, latency: Optional[str] = None):
        """Updates link status with timestamps in local SQLite."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO network_link_status (link, status, latency, last_checked, last_changed)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(link) DO UPDATE SET
                    status = excluded.status,
                    latency = excluded.latency,
                    last_checked = CURRENT_TIMESTAMP,
                    last_changed = CASE WHEN network_link_status.status != excluded.status THEN CURRENT_TIMESTAMP ELSE network_link_status.last_changed END;
                """,
                (link, status, latency),
            )
            conn.commit()

    def _backfill_editorial_publications(self, conn: sqlite3.Connection):
        """
        Backfills published articles from PostgreSQL (site-postgres public."Post")
        into editorial_publications to ensure only truly published content is tracked.
        """
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM editorial_publications")
            if cursor.fetchone()[0] > 0:
                return

            db_url = getattr(settings, "CHECK_LINKS_POSTGRES_URL", "")
            if not db_url:
                return

            import psycopg2
            with psycopg2.connect(db_url) as pg_conn:
                with pg_conn.cursor() as pg_cur:
                    pg_cur.execute(
                        """
                        SELECT id, title, category, slug, "publishedAt", "createdAt"
                        FROM "Post"
                        WHERE published = true
                        ORDER BY "createdAt" ASC
                        """
                    )
                    rows = pg_cur.fetchall()
                    for r in rows:
                        post_id, title, cat, slug, pub_at, cr_at = r
                        cat_upper = (cat or "").upper()
                        if "MUSICA" in cat_upper or "VIDA" in cat_upper:
                            categoria = "Música & Mercado Musical"
                        else:
                            categoria = "Tecnologia da Informação (TI)"

                        dt = pub_at or cr_at
                        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S") if hasattr(dt, "strftime") else str(dt)
                        task_id = f"site_post_{post_id}"
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO editorial_publications
                            (task_id, tema, categoria, angulo_editorial, doc_id, doc_url, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                task_id,
                                title,
                                categoria,
                                f"Publicado no site ({slug})",
                                None,
                                f"https://www.fernandonogueira.dev.br/blog/{slug}",
                                dt_str,
                            ),
                        )
            conn.commit()
            logger.info("Successfully backfilled editorial_publications from real published posts in site-postgres.")
        except Exception as e:
            logger.warning("Could not backfill editorial publications from site-postgres: %s", e)

    def record_editorial_publication(
        self,
        task_id: str,
        tema: str,
        categoria: str,
        angulo_editorial: Optional[str] = None,
        doc_id: Optional[str] = None,
        doc_url: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> bool:
        """
        Records or updates an approved and generated editorial publication in history.
        """
        with self._get_connection() as conn:
            if created_at:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO editorial_publications
                    (task_id, tema, categoria, angulo_editorial, doc_id, doc_url, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (task_id, tema, categoria, angulo_editorial, doc_id, doc_url, created_at),
                )
            else:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO editorial_publications
                    (task_id, tema, categoria, angulo_editorial, doc_id, doc_url, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (task_id, tema, categoria, angulo_editorial, doc_id, doc_url),
                )
            conn.commit()
            logger.info("Recorded editorial publication: '%s' (%s)", tema, categoria)
            return True

    def get_recent_editorial_publications(self, days: int = 7) -> List[Dict[str, Any]]:
        """
        Retrieves editorial publications produced in the last N days.
        Used by daily curation to prevent duplicate themes and ensure topical diversity.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, task_id, tema, categoria, angulo_editorial, doc_id, doc_url, created_at
                FROM editorial_publications
                WHERE created_at >= datetime('now', ?)
                ORDER BY created_at DESC
                """,
                (f"-{days} days",),
            )
            return [dict(row) for row in cursor.fetchall()]


repo = PipelineRepository()

