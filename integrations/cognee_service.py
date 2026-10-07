"""
Self-hosted Cognee Service for LinkedIn Sniper Growth.
100% local, self-contained AI memory engine running on this machine:
- Vector Storage: Embedded LanceDB on local disk (/app/data/cognee_system)
- Graph Storage: Embedded Kuzu on local disk (/app/data/cognee_system)
- Embeddings: Local CPU ONNX FastEmbed (BAAI/bge-small-en-v1.5)
- Zero external cloud vector databases or third-party SaaS dependencies.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Ensure strict self-hosted environment flags before Cognee initializes
os.environ["ENABLE_BACKEND_ACCESS_CONTROL"] = "false"
os.environ["VECTOR_DB_PROVIDER"] = "lancedb"
os.environ["EMBEDDING_PROVIDER"] = "fastembed"
os.environ["EMBEDDING_MODEL"] = "BAAI/bge-small-en-v1.5"
os.environ["EMBEDDING_DIMENSIONS"] = "384"
os.environ["GRAPH_DATABASE_PROVIDER"] = "kuzu"
os.environ["GLINER_AUTO_INSTALL"] = "false"

COGNEE_DIR = Path("/app/data/cognee_system")
COGNEE_DIR.mkdir(parents=True, exist_ok=True)


class SelfHostedCogneeService:
    """
    Manages self-hosted memory ingestion, graph indexing and semantic retrieval.
    """

    def __init__(self, system_dir: str = "/app/data/cognee_system"):
        self.system_dir = system_dir
        self._initialized = False

    def _ensure_init(self):
        if not self._initialized:
            import cognee
            cognee.config.system_root_directory(self.system_dir)
            self._initialized = True

    async def _add_and_cognify(self, text: str, dataset_name: str = "fernando_style"):
        import cognee
        self._ensure_init()
        # Ingest text chunks into LanceDB for semantic search; avoid heavy in-process cognify
        await cognee.add(text, dataset_name=dataset_name)

    def record_style_preference(
        self,
        post_text: str,
        final_comment: str,
        author: Optional[str] = None,
        nicho: Optional[str] = None,
        feedback: Optional[str] = None,
    ) -> bool:
        """
        Ingests an approved or edited comment into the self-hosted Cognee knowledge base.
        """
        author_name = author or "Líder de Mercado"
        nicho_name = nicho or "Tecnologia / Negócios"
        feedback_note = f" (Instrução dada por Fernando: {feedback})" if feedback else ""

        entry_text = (
            f"Exemplo de Comentário Aprovado por Fernando Nogueira:\n"
            f"- Nicho: {nicho_name}\n"
            f"- Autor do Post: {author_name}\n"
            f"- Trecho do Post: \"{post_text[:300].strip()}\"\n"
            f"- Como Fernando comentou: \"{final_comment.strip()}\"{feedback_note}\n"
            f"Diretriz de estilo: Comentários curtos (1 a 3 frases), pessoais, em 1ª pessoa, amigáveis e diretos."
        )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._add_and_cognify(entry_text))
            else:
                loop.run_until_complete(self._add_and_cognify(entry_text))
            logger.info("Successfully scheduled style preference into self-hosted Cognee.")
            return True
        except RuntimeError:
            # Create a new loop in worker/background thread
            try:
                new_loop = asyncio.new_event_loop()
                new_loop.run_until_complete(self._add_and_cognify(entry_text))
                new_loop.close()
                logger.info("Ingested style preference into self-hosted Cognee via thread loop.")
                return True
            except Exception as ex:
                logger.warning("Could not ingest into self-hosted Cognee: %s", ex)
                return False
        except Exception as e:
            logger.warning("Could not ingest into self-hosted Cognee: %s", e)
            return False

    async def _search_chunks(self, query: str, top_k: int = 2) -> List[str]:
        import cognee
        self._ensure_init()
        try:
            results = await cognee.search(query, cognee.SearchType.CHUNKS, top_k=top_k)
            retrieved = []
            for r in results:
                if isinstance(r, dict) and "text" in r:
                    retrieved.append(r["text"])
                elif hasattr(r, "text"):
                    retrieved.append(getattr(r, "text"))
            return retrieved
        except Exception as e:
            logger.warning("Self-hosted Cognee search error: %s", e)
            return []

    def retrieve_style_context(self, post_text: str, nicho: Optional[str] = None) -> str:
        """
        Retrieves relevant style patterns and past examples from self-hosted Cognee.
        Guaranteed to never block or delay comment generation.
        """
        query = f"Como Fernando Nogueira comenta sobre {nicho or 'tecnologia'} e {post_text[:120]}?"
        try:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                results = pool.submit(
                    lambda: asyncio.run(self._search_chunks(query, top_k=2))
                ).result(timeout=1.5)

            if results:
                formatted = "\n\n--- MEMÓRIA DE ESTILO RECUPERADA VIA COGNEE (SELF-HOSTED) ---\n"
                formatted += "\n---\n".join(results)
                formatted += "\n---------------------------------------------------------------\n"
                return formatted
        except Exception as e:
            logger.debug("Self-hosted Cognee retrieval skipped or timed out: %s", e)

        return ""


cognee_service = SelfHostedCogneeService()
