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

                CREATE TABLE IF NOT EXISTS editorial_pautas_pool (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uuid TEXT UNIQUE NOT NULL,
                    titulo TEXT NOT NULL,
                    categoria TEXT NOT NULL,
                    origem TEXT NOT NULL DEFAULT 'DAILY_CURATION',
                    angulo_editorial TEXT,
                    sintese_factual TEXT,
                    roteiro_topicos TEXT,
                    fontes TEXT,
                    status TEXT NOT NULL DEFAULT 'DISPONIVEL',
                    task_id TEXT,
                    action_token TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    selected_at TIMESTAMP,
                    published_at TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_pautas_pool_status ON editorial_pautas_pool(status);
                CREATE INDEX IF NOT EXISTS idx_pautas_pool_categoria ON editorial_pautas_pool(categoria);
                CREATE INDEX IF NOT EXISTS idx_pautas_pool_created ON editorial_pautas_pool(created_at);
            """)
            conn.commit()
            self._backfill_editorial_publications(conn)
            self._backfill_editorial_pautas_pool(conn)

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
            # Sync status in editorial_pautas_pool if matching pauta exists
            try:
                conn.execute(
                    """
                    UPDATE editorial_pautas_pool
                    SET status = 'PUBLICADO', published_at = CURRENT_TIMESTAMP
                    WHERE LOWER(TRIM(titulo)) = LOWER(TRIM(?))
                    """,
                    (tema,),
                )
            except Exception as e_sync:
                logger.debug("Could not sync pauta pool status: %s", e_sync)

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

    def _backfill_editorial_pautas_pool(self, conn: sqlite3.Connection):
        """
        Backfills past curated pautas from flow_executions into editorial_pautas_pool.
        Cross-checks with editorial_publications to identify already published topics.
        """
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM editorial_pautas_pool")
            if cursor.fetchone()[0] > 0:
                return

            import uuid as uuid_mod

            # Fetch published topics for cross-matching
            pub_cursor = conn.execute("SELECT tema FROM editorial_publications")
            published_temas = [row[0].strip().lower() for row in pub_cursor.fetchall() if row[0]]

            # Fetch curation runs
            exec_cursor = conn.execute(
                """
                SELECT task_id, output_payload, created_at
                FROM flow_executions
                WHERE flow_name = 'daily_editorial_curation' AND status = 'SUCCESS'
                ORDER BY created_at ASC
                """
            )
            rows = exec_cursor.fetchall()
            logger.info("Found %d daily curation executions to backfill into editorial_pautas_pool.", len(rows))

            count_inserted = 0
            for task_id, output_payload, created_at in rows:
                if not output_payload:
                    continue
                try:
                    data = json.loads(output_payload)
                    pautas = data.get("pautas", [])
                    for idx, p in enumerate(pautas):
                        titulo = (p.get("titulo") or "").strip()
                        if not titulo:
                            continue

                        categoria = p.get("categoria") or "Tecnologia da Informação (TI)"
                        angulo = p.get("angulo_editorial") or ""
                        sintese = p.get("sintese_fiel_das_materias") or ""
                        topicos = p.get("topicos_para_redacao") or []
                        fontes = p.get("fontes_relacionadas") or []

                        # Check if matches any published article
                        titulo_lower = titulo.lower()
                        is_published = False
                        for pub in published_temas:
                            if titulo_lower == pub or titulo_lower in pub or pub in titulo_lower:
                                is_published = True
                                break
                            w1 = set(titulo_lower.split())
                            w2 = set(pub.split())
                            if len(w1 & w2) >= 4 and len(w1 & w2) / max(len(w1), 1) > 0.5:
                                is_published = True
                                break

                        status = "PUBLICADO" if is_published else "DISPONIVEL"
                        p_uuid = f"pauta_{uuid_mod.uuid4().hex[:12]}"

                        conn.execute(
                            """
                            INSERT INTO editorial_pautas_pool
                            (uuid, titulo, categoria, origem, angulo_editorial, sintese_factual, roteiro_topicos, fontes, status, task_id, created_at)
                            VALUES (?, ?, ?, 'DAILY_CURATION', ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                p_uuid,
                                titulo,
                                categoria,
                                angulo,
                                sintese,
                                json.dumps(topicos, ensure_ascii=False),
                                json.dumps(fontes, ensure_ascii=False),
                                status,
                                task_id,
                                created_at,
                            ),
                        )
                        count_inserted += 1
                except Exception as err_p:
                    logger.warning("Error parsing pautas in execution %s: %s", task_id, err_p)

            conn.commit()
            logger.info("Successfully backfilled %d pautas into editorial_pautas_pool.", count_inserted)
        except Exception as e:
            logger.warning("Could not backfill editorial_pautas_pool: %s", e)

    def save_editorial_pauta(
        self,
        titulo: str,
        categoria: str,
        origem: str = "DAILY_CURATION",
        angulo_editorial: Optional[str] = None,
        sintese_factual: Optional[str] = None,
        roteiro_topicos: Optional[List[str]] = None,
        fontes: Optional[List[Any]] = None,
        status: str = "DISPONIVEL",
        task_id: Optional[str] = None,
        action_token: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Saves a new pauta into editorial_pautas_pool.
        """
        import uuid as uuid_mod
        p_uuid = f"pauta_{uuid_mod.uuid4().hex[:12]}"
        roteiro_json = json.dumps(roteiro_topicos or [], ensure_ascii=False)
        fontes_json = json.dumps(fontes or [], ensure_ascii=False)

        with self._get_connection() as conn:
            if created_at:
                cursor = conn.execute(
                    """
                    INSERT INTO editorial_pautas_pool
                    (uuid, titulo, categoria, origem, angulo_editorial, sintese_factual, roteiro_topicos, fontes, status, task_id, action_token, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (p_uuid, titulo, categoria, origem, angulo_editorial, sintese_factual, roteiro_json, fontes_json, status, task_id, action_token, created_at),
                )
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO editorial_pautas_pool
                    (uuid, titulo, categoria, origem, angulo_editorial, sintese_factual, roteiro_topicos, fontes, status, task_id, action_token)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (p_uuid, titulo, categoria, origem, angulo_editorial, sintese_factual, roteiro_json, fontes_json, status, task_id, action_token),
                )
            conn.commit()
            pauta_id = cursor.lastrowid
            logger.info("Saved editorial pauta #%d ('%s') with status '%s'", pauta_id, titulo, status)
            return {
                "id": pauta_id,
                "uuid": p_uuid,
                "titulo": titulo,
                "categoria": categoria,
                "origem": origem,
                "status": status,
            }

    def list_editorial_pautas_pool(
        self,
        status: Optional[str] = None,
        categoria: Optional[str] = None,
        origem: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Queries pautas from the pool with optional status, category, origin and keyword filters.
        """
        query = "SELECT * FROM editorial_pautas_pool WHERE 1=1"
        params: List[Any] = []

        if status and status.upper() != "ALL":
            query += " AND status = ?"
            params.append(status.upper())
        elif not status:
            # Default to not showing archived unless requested
            query += " AND status != 'ARQUIVADO'"

        if categoria and categoria.upper() != "ALL":
            if "TI" in categoria.upper():
                query += " AND (categoria LIKE '%TI%' OR categoria LIKE '%Tecnologia%')"
            elif "MUSICA" in categoria.upper() or "MÚSICA" in categoria.upper():
                query += " AND (categoria LIKE '%Música%' OR categoria LIKE '%Musica%')"
            else:
                query += " AND categoria = ?"
                params.append(categoria)

        if origem and origem.upper() != "ALL":
            query += " AND origem = ?"
            params.append(origem.upper())

        if search:
            query += " AND (titulo LIKE ? OR angulo_editorial LIKE ? OR sintese_factual LIKE ?)"
            term = f"%{search}%"
            params.extend([term, term, term])

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for r in rows:
                item = dict(r)
                if item.get("roteiro_topicos"):
                    try:
                        item["roteiro_topicos"] = json.loads(item["roteiro_topicos"])
                    except Exception:
                        pass
                if item.get("fontes"):
                    try:
                        item["fontes"] = json.loads(item["fontes"])
                    except Exception:
                        pass
                results.append(item)
            return results

    def get_editorial_pauta(self, pauta_id_or_uuid: Any) -> Optional[Dict[str, Any]]:
        """
        Retrieves a single pauta by ID (int) or UUID (str).
        """
        with self._get_connection() as conn:
            if isinstance(pauta_id_or_uuid, int) or (isinstance(pauta_id_or_uuid, str) and pauta_id_or_uuid.isdigit()):
                cursor = conn.execute("SELECT * FROM editorial_pautas_pool WHERE id = ?", (int(pauta_id_or_uuid),))
            else:
                cursor = conn.execute("SELECT * FROM editorial_pautas_pool WHERE uuid = ?", (str(pauta_id_or_uuid),))
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            if item.get("roteiro_topicos"):
                try:
                    item["roteiro_topicos"] = json.loads(item["roteiro_topicos"])
                except Exception:
                    pass
            if item.get("fontes"):
                try:
                    item["fontes"] = json.loads(item["fontes"])
                except Exception:
                    pass
            return item

    def update_editorial_pauta_status(
        self,
        pauta_id_or_uuid: Any,
        status: str,
        selected_at: Optional[str] = None,
        published_at: Optional[str] = None,
    ) -> bool:
        """
        Updates the status and timestamps of a pauta in the pool.
        """
        with self._get_connection() as conn:
            target_col = "id" if (isinstance(pauta_id_or_uuid, int) or (isinstance(pauta_id_or_uuid, str) and pauta_id_or_uuid.isdigit())) else "uuid"
            target_val = int(pauta_id_or_uuid) if target_col == "id" else str(pauta_id_or_uuid)

            updates = ["status = ?"]
            params = [status.upper()]

            if status.upper() == "EM_PRODUCAO":
                updates.append("selected_at = COALESCE(?, CURRENT_TIMESTAMP)")
                params.append(selected_at)
            elif status.upper() == "PUBLICADO":
                updates.append("published_at = COALESCE(?, CURRENT_TIMESTAMP)")
                params.append(published_at)

            params.append(target_val)
            sql = f"UPDATE editorial_pautas_pool SET {', '.join(updates)} WHERE {target_col} = ?"
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.rowcount > 0

    def get_editorial_pautas_stats(self) -> Dict[str, int]:
        """
        Returns summary statistics of the editorial pautas pool.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'DISPONIVEL' THEN 1 ELSE 0 END) as disponiveis,
                    SUM(CASE WHEN status = 'EM_PRODUCAO' THEN 1 ELSE 0 END) as em_producao,
                    SUM(CASE WHEN status = 'PUBLICADO' THEN 1 ELSE 0 END) as publicados,
                    SUM(CASE WHEN status = 'ARQUIVADO' THEN 1 ELSE 0 END) as arquivados,
                    SUM(CASE WHEN (categoria LIKE '%TI%' OR categoria LIKE '%Tecnologia%') AND status = 'DISPONIVEL' THEN 1 ELSE 0 END) as ti_disponiveis,
                    SUM(CASE WHEN (categoria LIKE '%Música%' OR categoria LIKE '%Musica%') AND status = 'DISPONIVEL' THEN 1 ELSE 0 END) as musica_disponiveis,
                    SUM(CASE WHEN origem = 'MANUAL' THEN 1 ELSE 0 END) as manuais
                FROM editorial_pautas_pool
                """
            )
            row = cursor.fetchone()
            if not row:
                return {}
            return {
                "total": row["total"] or 0,
                "disponiveis": row["disponiveis"] or 0,
                "em_producao": row["em_producao"] or 0,
                "publicados": row["publicados"] or 0,
                "arquivados": row["arquivados"] or 0,
                "ti_disponiveis": row["ti_disponiveis"] or 0,
                "musica_disponiveis": row["musica_disponiveis"] or 0,
                "manuais": row["manuais"] or 0,
            }


repo = PipelineRepository()

