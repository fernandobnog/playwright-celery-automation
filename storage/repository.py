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

                CREATE TABLE IF NOT EXISTS linkedin_growth_targets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    nicho TEXT NOT NULL,
                    linkedin_url TEXT UNIQUE NOT NULL,
                    descricao TEXT,
                    ultimo_post_id TEXT,
                    ultimo_check TIMESTAMP,
                    ativo BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS linkedin_growth_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER,
                    target_nome TEXT NOT NULL,
                    post_url TEXT NOT NULL UNIQUE,
                    post_texto TEXT NOT NULL,
                    post_autor TEXT,
                    comentario_gerado TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL',
                    action_token TEXT UNIQUE,
                    published_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(target_id) REFERENCES linkedin_growth_targets(id)
                );

                CREATE TABLE IF NOT EXISTS linkedin_style_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    comment_id INTEGER,
                    post_url TEXT,
                    post_texto TEXT NOT NULL,
                    post_autor TEXT,
                    nicho TEXT,
                    sugestao_ia TEXT NOT NULL,
                    comentario_final TEXT NOT NULL,
                    instrucao_ajuste TEXT,
                    foi_editado BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS linkedin_autopilot_prospects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    headline TEXT,
                    profile_url TEXT UNIQUE NOT NULL,
                    source_target_nome TEXT,
                    source_post_url TEXT,
                    nicho TEXT,
                    status TEXT NOT NULL DEFAULT 'DISCOVERED',
                    invite_note TEXT,
                    invited_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_linkedin_targets_ativo ON linkedin_growth_targets(ativo);
                CREATE INDEX IF NOT EXISTS idx_linkedin_comments_status ON linkedin_growth_comments(status);
                CREATE INDEX IF NOT EXISTS idx_linkedin_style_memory_nicho ON linkedin_style_memory(nicho);
                CREATE INDEX IF NOT EXISTS idx_autopilot_prospects_status ON linkedin_autopilot_prospects(status);
                CREATE INDEX IF NOT EXISTS idx_autopilot_prospects_url ON linkedin_autopilot_prospects(profile_url);
            """)
            conn.commit()
            self._backfill_editorial_publications(conn)
            self._backfill_editorial_pautas_pool(conn)
            self._seed_default_linkedin_targets(conn)

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

    def _seed_default_linkedin_targets(self, conn: sqlite3.Connection):
        """Seeds the curated high-influence targets if table is empty."""
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM linkedin_growth_targets")
            if cursor.fetchone()[0] == 0:
                targets = [
                    # TI Jurídico & Legaltech / LegalOps
                    ("Daniel Becker", "TI_JURIDICO", "https://www.linkedin.com/in/danielbeckerpinto/", "Sócio BBL Advogados, Top Voice, Diretor de Novas Tecnologias, LegalOps e IA Jurídica (>40k)"),
                    ("Paulo Samico", "TI_JURIDICO", "https://www.linkedin.com/in/paulo-samico/", "Gerente Jurídico, Top Voice em Inovação Jurídica e Gestão Legal (>30k)"),
                    ("Bruno Feigelson", "TI_JURIDICO", "https://www.linkedin.com/in/bruno-feigelson/", "Cofundador da AB2L (Lawtechs), Futurista Jurídico e Investidor (>45k)"),
                    ("Erik Fontenele Nybo", "TI_JURIDICO", "https://www.linkedin.com/in/erikfontenelenybo/", "Fundador da Bits Academy, Legal Design e IA prática (>30k)"),
                    # Advocacia Corporativa, Cibersegurança & Direito Digital
                    ("Patrícia Peck", "ADVOCACIA", "https://www.linkedin.com/in/patriciapeckpinheiro/", "Sócia Peck Advogados, Conselheira ANPD, Pioneira em Direito Digital e Segurança (>100k)"),
                    ("Renato Opice Blum", "ADVOCACIA", "https://www.linkedin.com/in/renatoopiceblum/", "Sócio Opice Blum Advogados, Top Voice Direito Digital e IA nos Tribunais (>70k)"),
                    ("Camilla Jimene", "ADVOCACIA", "https://www.linkedin.com/in/camilla-jimene-55648363/", "Sócia Opice Blum, Especialista em Cibersegurança e Resposta a Incidentes (>30k)"),
                    ("Alexandre Atheniense", "ADVOCACIA", "https://www.linkedin.com/in/atheniense/", "Pioneiro em Direito e Tecnologia, Governança de Dados e IA (>25k)"),
                    # RH, Gestão de Pessoas & Futuro do Trabalho
                    ("Léo Oliveira", "RH", "https://www.linkedin.com/in/leooliveirahr/", "CEO Humanos & IA, Top Voice IA em RH e Transformação Cultural (>50k)"),
                    ("Sofia Esteves", "RH", "https://www.linkedin.com/in/estevessofia/", "Presidente do Conselho Cia de Talentos, Top Voice RH e Carreira (>200k)"),
                    ("Ruy Shiozawa", "RH", "https://www.linkedin.com/in/ruyshiozawa/", "Ex-CEO Great Place to Work (GPTW) Brasil, Cultura e Clima (>80k)"),
                    ("Gabriela Augusto", "RH", "https://www.linkedin.com/in/gabriela-augusto-a30466195/", "Fundadora Transcendemos, Top Voice Consultoria Corporativa e Inclusão (>40k)"),
                    # Inovação Jurídica, LegalTech & IA no Direito (Expansão Estratégica)
                    ("Ronaldo Lemos", "TI_JURIDICO", "https://www.linkedin.com/in/ronaldolemos/", "Advogado, Diretor do ITS Rio, Especialista em IA, Direito Digital e Políticas Públicas (>100k)"),
                    ("Luciana Stegagno", "TI_JURIDICO", "https://www.linkedin.com/in/lucianastegagno/", "Head de Legal Operations & Inovação Jurídica, Top Voice LegalOps (>25k)"),
                    ("Juliana Ono", "TI_JURIDICO", "https://www.linkedin.com/in/juliana-ono/", "Diretora de Conteúdo Thomson Reuters, Especialista em LegalTech e Gestão (>20k)"),
                    ("Felipe Asensi", "TI_JURIDICO", "https://www.linkedin.com/in/felipe-asensi/", "Fundador Global Academy, Inovação, Carreira e Tecnologia no Direito (>50k)"),
                    # Liderança Tecnológica, Governança & IA Corporativa
                    ("Paulo Silveira", "LIDERANCA_TI", "https://www.linkedin.com/in/paulosilveira/", "CEO Grupo Alura / Hipsters.tech, Liderança Técnica, IA e Educação Corporativa (>150k)"),
                    ("Cezar Taurion", "LIDERANCA_TI", "https://www.linkedin.com/in/ctaurion/", "Chief Strategy Officer Redpill Innovation, Autor, Estratégia de IA e Computação em Nuvem (>80k)"),
                    ("Silvio Meira", "LIDERANCA_TI", "https://www.linkedin.com/in/silviomeira/", "Cientista-chefe TDS.company, Professor Emérito UFPE, Futurismo e Transformação Digital (>150k)"),
                ]
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO linkedin_growth_targets (nome, nicho, linkedin_url, descricao)
                    VALUES (?, ?, ?, ?)
                    """,
                    targets,
                )
                conn.commit()
                logger.info("Seeded %d default curated LinkedIn growth targets.", len(targets))
        except Exception as e:
            logger.warning("Could not seed default LinkedIn targets: %s", e)

    def get_active_linkedin_targets(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns active LinkedIn targets ordered by last check timestamp."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, nome, nicho, linkedin_url, descricao, ultimo_post_id, ultimo_check, ativo, created_at
                FROM linkedin_growth_targets
                WHERE ativo = 1
                ORDER BY ultimo_check ASC NULLS FIRST, id ASC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_all_linkedin_targets(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Returns all targets (active or paused) ordered by id."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, nome, nicho, linkedin_url, descricao, ultimo_post_id, ultimo_check, ativo, created_at
                FROM linkedin_growth_targets
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def add_linkedin_target(self, nome: str, nicho: str, linkedin_url: str, descricao: Optional[str] = None) -> int:
        """Adds a new target to radar."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO linkedin_growth_targets (nome, nicho, linkedin_url, descricao, ativo)
                VALUES (?, ?, ?, ?, 1)
                """,
                (nome.strip(), nicho.strip().upper(), linkedin_url.strip(), (descricao or "").strip()),
            )
            conn.commit()
            return cursor.lastrowid

    def toggle_linkedin_target(self, target_id: int) -> bool:
        """Toggles active/inactive state of a target."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE linkedin_growth_targets SET ativo = CASE WHEN ativo = 1 THEN 0 ELSE 1 END WHERE id = ?",
                (target_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete_linkedin_target(self, target_id: int) -> bool:
        """Deletes a target profile."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM linkedin_growth_targets WHERE id = ?", (target_id,))
            conn.commit()
            return cursor.rowcount > 0

    def update_target_last_check(self, target_id: int, last_post_id: Optional[str] = None):
        """Updates last_check timestamp and latest post ID for a target."""
        with self._get_connection() as conn:
            if last_post_id:
                conn.execute(
                    "UPDATE linkedin_growth_targets SET ultimo_check = CURRENT_TIMESTAMP, ultimo_post_id = ? WHERE id = ?",
                    (last_post_id, target_id),
                )
            else:
                conn.execute(
                    "UPDATE linkedin_growth_targets SET ultimo_check = CURRENT_TIMESTAMP WHERE id = ?",
                    (target_id,),
                )
            conn.commit()

    def save_linkedin_growth_comment(
        self,
        target_id: int,
        target_nome: str,
        post_url: str,
        post_texto: str,
        post_autor: Optional[str],
        comentario_gerado: str,
        action_token: Optional[str] = None,
    ) -> int:
        """Saves a generated sniper comment for approval."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO linkedin_growth_comments (
                    target_id, target_nome, post_url, post_texto, post_autor,
                    comentario_gerado, status, action_token
                ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING_APPROVAL', ?)
                ON CONFLICT(post_url) DO UPDATE SET
                    comentario_gerado = excluded.comentario_gerado,
                    action_token = COALESCE(excluded.action_token, linkedin_growth_comments.action_token)
                """,
                (
                    target_id,
                    target_nome,
                    post_url,
                    post_texto,
                    post_autor or target_nome,
                    comentario_gerado,
                    action_token,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_linkedin_growth_comment_by_id(self, comment_id: int) -> Optional[Dict[str, Any]]:
        """Finds comment record by primary key."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM linkedin_growth_comments WHERE id = ?",
                (comment_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_linkedin_growth_comment_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Finds comment record by action token."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM linkedin_growth_comments WHERE action_token = ?",
                (token,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_linkedin_growth_comment_status(
        self,
        comment_id: int,
        status: str,
        published_at: Optional[str] = None,
    ) -> bool:
        """Updates comment workflow status (e.g. APPROVED, PUBLISHED, REJECTED)."""
        with self._get_connection() as conn:
            if status.upper() == "PUBLISHED":
                cursor = conn.execute(
                    "UPDATE linkedin_growth_comments SET status = ?, published_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (status.upper(), comment_id),
                )
            else:
                cursor = conn.execute(
                    "UPDATE linkedin_growth_comments SET status = ? WHERE id = ?",
                    (status.upper(), comment_id),
                )
            conn.commit()
            return cursor.rowcount > 0

    def get_pending_linkedin_growth_comments(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieves comments awaiting human review."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM linkedin_growth_comments
                WHERE status = 'PENDING_APPROVAL'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_all_linkedin_growth_comments(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves recent comments regardless of status."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM linkedin_growth_comments
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def update_linkedin_growth_comment_text(self, comment_id: int, new_text: str) -> bool:
        """Updates the comment generated text before approval/publication."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE linkedin_growth_comments SET comentario_gerado = ? WHERE id = ?",
                (new_text.strip(), comment_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def record_style_memory(
        self,
        comment_id: int,
        final_comment: str,
        instrucao: Optional[str] = None,
        foi_editado: Optional[bool] = None,
    ) -> bool:
        """
        Stores an approved or edited comment into the style memory so the AI
        learns to write exactly like Fernando over time.
        """
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT c.*, t.nicho 
                FROM linkedin_growth_comments c
                LEFT JOIN linkedin_growth_targets t ON c.target_id = t.id
                WHERE c.id = ?
                """,
                (comment_id,),
            ).fetchone()
            if not row:
                return False

            original_suggestion = row["comentario_gerado"] or ""
            if foi_editado is not None:
                is_edited_val = 1 if foi_editado else 0
            else:
                is_edited_val = 1 if (instrucao or final_comment.strip() != original_suggestion.strip()) else 0

            conn.execute(
                """
                INSERT INTO linkedin_style_memory (
                    comment_id, post_url, post_texto, post_autor, nicho,
                    sugestao_ia, comentario_final, instrucao_ajuste, foi_editado
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    comment_id,
                    row["post_url"],
                    row["post_texto"],
                    row["post_autor"],
                    row["nicho"] if "nicho" in row.keys() else "GERAL",
                    original_suggestion,
                    final_comment.strip(),
                    instrucao,
                    is_edited_val,
                ),
            )
            conn.commit()

            # Record in self-hosted Cognee (Vector + Graph) for semantic recall
            try:
                from integrations.cognee_service import cognee_service
                cognee_service.record_style_preference(
                    post_text=row["post_texto"] or "",
                    final_comment=final_comment.strip(),
                    author=row["post_autor"],
                    nicho=row["nicho"] if "nicho" in row.keys() else "GERAL",
                    feedback=instrucao,
                )
            except Exception as cognee_err:
                logger.debug("Could not record preference in Cognee: %s", cognee_err)

            return True

    def get_style_examples(self, limit: int = 3, nicho: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieves recent authentic comment examples (prioritizing human-edited ones)
        to inject as few-shot in-context learning.
        Strictly excludes any examples with questions, 'bancada', or clichéd openers.
        """
        filter_sql = """
            AND comentario_final NOT LIKE '%?%'
            AND LOWER(comentario_final) NOT LIKE '%bancada%'
            AND LOWER(comentario_final) NOT LIKE 'ponto cirúrgico%'
            AND LOWER(comentario_final) NOT LIKE 'excelente%'
        """
        with self._get_connection() as conn:
            if nicho:
                cursor = conn.execute(
                    f"""
                    SELECT post_texto, post_autor, comentario_final, foi_editado
                    FROM linkedin_style_memory
                    WHERE nicho = ? {filter_sql}
                    ORDER BY foi_editado DESC, id DESC
                    LIMIT ?
                    """,
                    (nicho, limit),
                )
                rows = cursor.fetchall()
                if len(rows) >= limit:
                    return [dict(r) for r in rows]

            # Fallback / General
            cursor = conn.execute(
                f"""
                SELECT post_texto, post_autor, comentario_final, foi_editado
                FROM linkedin_style_memory
                WHERE 1=1 {filter_sql}
                ORDER BY foi_editado DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_linkedin_growth_stats(self) -> Dict[str, int]:
        """Summary metrics for the LinkedIn Growth engine."""
        with self._get_connection() as conn:
            row_targets = conn.execute(
                "SELECT COUNT(*) as total_targets, SUM(CASE WHEN ativo=1 THEN 1 ELSE 0 END) as active_targets FROM linkedin_growth_targets"
            ).fetchone()
            row_comments = conn.execute(
                """
                SELECT
                    COUNT(*) as total_comments,
                    SUM(CASE WHEN status = 'PENDING_APPROVAL' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status = 'APPROVED' THEN 1 ELSE 0 END) as approved,
                    SUM(CASE WHEN status = 'PUBLISHED' THEN 1 ELSE 0 END) as published,
                    SUM(CASE WHEN status = 'LIKED' THEN 1 ELSE 0 END) as liked,
                    SUM(CASE WHEN status = 'FAILED_PUBLISH' THEN 1 ELSE 0 END) as failed
                FROM linkedin_growth_comments
                """
            ).fetchone()
            return {
                "total_targets": (row_targets["total_targets"] if row_targets else 0) or 0,
                "active_targets": (row_targets["active_targets"] if row_targets else 0) or 0,
                "total_comments": (row_comments["total_comments"] if row_comments else 0) or 0,
                "pending_comments": (row_comments["pending"] if row_comments else 0) or 0,
                "approved_comments": (row_comments["approved"] if row_comments else 0) or 0,
                "published_comments": (row_comments["published"] if row_comments else 0) or 0,
                "liked_comments": (row_comments["liked"] if row_comments else 0) or 0,
                "failed_comments": (row_comments["failed"] if row_comments else 0) or 0,
            }

    # ─── LinkedIn Connection Autopilot ──────────────────────────────────────────

    def save_autopilot_prospect(
        self,
        nome: str,
        headline: Optional[str],
        profile_url: str,
        source_target_nome: Optional[str] = None,
        source_post_url: Optional[str] = None,
        nicho: Optional[str] = None,
    ) -> Optional[int]:
        """Saves a discovered prospect to autopilot queue if not already present."""
        clean_url = profile_url.split("?")[0].rstrip("/")
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO linkedin_autopilot_prospects 
                (nome, headline, profile_url, source_target_nome, source_post_url, nicho, status)
                VALUES (?, ?, ?, ?, ?, ?, 'DISCOVERED')
                """,
                (nome.strip(), (headline or "").strip(), clean_url, source_target_nome, source_post_url, nicho),
            )
            conn.commit()
            return cursor.lastrowid if cursor.rowcount > 0 else None

    def get_pending_autopilot_prospects(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Retrieves discovered prospects ready to receive connection invites."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM linkedin_autopilot_prospects
                WHERE status = 'DISCOVERED'
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_daily_autopilot_invites_count(self) -> int:
        """Returns the number of connection invites sent today."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM linkedin_autopilot_prospects
                WHERE status = 'INVITED' AND date(invited_at) = date('now')
                """
            )
            return cursor.fetchone()[0]

    def update_autopilot_prospect_status(
        self,
        prospect_id: int,
        status: str,
        invite_note: Optional[str] = None,
    ) -> bool:
        """Updates prospect status (e.g. INVITED, ALREADY_CONNECTED, FAILED, SKIPPED)."""
        with self._get_connection() as conn:
            if status.upper() == "INVITED":
                cursor = conn.execute(
                    """
                    UPDATE linkedin_autopilot_prospects
                    SET status = ?, invite_note = ?, invited_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (status.upper(), invite_note, prospect_id),
                )
            else:
                cursor = conn.execute(
                    "UPDATE linkedin_autopilot_prospects SET status = ? WHERE id = ?",
                    (status.upper(), prospect_id),
                )
            conn.commit()
            return cursor.rowcount > 0

    def get_autopilot_stats(self) -> Dict[str, Any]:
        """Returns overall metrics for connection autopilot."""
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM linkedin_autopilot_prospects").fetchone()[0]
            invited = conn.execute("SELECT COUNT(*) FROM linkedin_autopilot_prospects WHERE status = 'INVITED'").fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM linkedin_autopilot_prospects WHERE status = 'DISCOVERED'").fetchone()[0]
            today_invited = self.get_daily_autopilot_invites_count()
            return {
                "total_discovered": total,
                "total_invited": invited,
                "pending_queue": pending,
                "today_invited": today_invited,
            }

    def get_all_autopilot_prospects(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves most recent autopilot prospects."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM linkedin_autopilot_prospects
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in cursor.fetchall()]



repo = PipelineRepository()


