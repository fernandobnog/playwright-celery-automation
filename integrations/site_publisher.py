"""
Site Publication Client.
Publishes approved articles directly into the site's PostgreSQL database (site-postgres public."Post"),
making them instantly live on https://www.fernandonogueira.dev.br/blog/{slug}.
"""

from datetime import datetime
import json
import logging
import re
from typing import Any, Dict, List, Optional
import unicodedata
import psycopg2

from core.config import settings

logger = logging.getLogger(__name__)


def slugify(text: str) -> str:
    """Generates clean SEO-friendly slug from title."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("utf-8")
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[-\s]+", "-", text)


class SitePublisher:
    """
    Handles direct, atomic publishing to the website's database.
    """

    def __init__(self, postgres_url: Optional[str] = None):
        self.db_url = postgres_url or settings.CHECK_LINKS_POSTGRES_URL

    def _get_connection(self):
        if not self.db_url:
            raise ValueError("PostgreSQL database URL is not configured (CHECK_LINKS_POSTGRES_URL).")
        return psycopg2.connect(self.db_url)

    def publish_post(
        self,
        title: str,
        content: str,
        excerpt: str,
        category: str = "TECNOLOGIA",
        tags: Optional[List[str]] = None,
        read_time: int = 5,
        slug: Optional[str] = None,
        cover_image: Optional[str] = None,
        is_published: bool = True,
    ) -> Dict[str, Any]:
        """
        Inserts or updates an article in public."Post".
        Sets published = True and publishedAt = NOW() to go live immediately.
        """
        clean_title = (title or "Artigo Sem Título").strip()
        final_slug = (slug or slugify(clean_title)).strip()
        clean_excerpt = (excerpt or "").strip()
        clean_content = (content or "").strip()
        tags_list = tags or ["Tecnologia", "Inovação"]
        tags_json = json.dumps(tags_list)

        # Normalize category
        cat_upper = (category or "").upper()
        if "MÚSICA" in cat_upper or "MUSICA" in cat_upper:
            normalized_cat = "MÚSICA"
        elif "TI" in cat_upper or "TECNOLOGIA" in cat_upper:
            normalized_cat = "TECNOLOGIA"
        else:
            normalized_cat = category.strip() or "GERAL"

        logger.info("Publishing article to site: '%s' (slug: %s)", clean_title, final_slug)

        with self._get_connection() as conn:
            with conn.cursor() as cur:
                # Check if post with this slug exists
                cur.execute('SELECT id FROM public."Post" WHERE slug = %s LIMIT 1;', (final_slug,))
                row = cur.fetchone()

                now = datetime.now()
                if row:
                    post_id = row[0]
                    cur.execute(
                        """
                        UPDATE public."Post"
                        SET title = %s, excerpt = %s, content = %s, category = %s,
                            tags = %s, "readTime" = %s, "coverImage" = %s,
                            published = %s, "publishedAt" = COALESCE("publishedAt", %s),
                            "updatedAt" = %s
                        WHERE id = %s
                        RETURNING id, slug;
                        """,
                        (
                            clean_title,
                            clean_excerpt,
                            clean_content,
                            normalized_cat,
                            tags_json,
                            read_time,
                            cover_image,
                            is_published,
                            now,
                            now,
                            post_id,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO public."Post" (
                            slug, title, excerpt, content, category, tags,
                            "readTime", "coverImage", published, "publishedAt",
                            "createdAt", "updatedAt"
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id, slug;
                        """,
                        (
                            final_slug,
                            clean_title,
                            clean_excerpt,
                            clean_content,
                            normalized_cat,
                            tags_json,
                            read_time,
                            cover_image,
                            is_published,
                            now if is_published else None,
                            now,
                            now,
                        ),
                    )
                    inserted = cur.fetchone()
                    post_id = inserted[0]

                conn.commit()

        post_url = f"https://www.fernandonogueira.dev.br/blog/{final_slug}"
        logger.info("Article successfully published to site (ID: %s, URL: %s)", post_id, post_url)

        # Trigger Zero-Fetch static compilation on host to make article immediately live
        rebuild_result = {"status": "SKIPPED"}
        if is_published:
            rebuild_result = self.trigger_static_rebuild()

        return {
            "status": "SUCCESS",
            "post_id": post_id,
            "slug": final_slug,
            "url": post_url,
            "published_at": now.isoformat(),
            "rebuild": rebuild_result,
        }

    def trigger_static_rebuild(self) -> Dict[str, Any]:
        """
        Triggers the host's site-rebuild-daemon to sync published posts and recompile
        the static Zero-Fetch HTML in dist/. Ensures newly published posts appear live immediately.
        """
        import requests

        key = (
            getattr(settings, "INTERNAL_GATEWAY_KEY", None)
            or getattr(settings, "INTERNAL_API_KEY", None)
            or "37e77cd994f94a5f3f8a183a28d4a14fd87f8a1c14933e31bd3f5bb7f118c78f"
        )
        headers = {"X-Internal-Gateway-Key": key}

        # Endpoints matching possible host bridge gateway IPs
        candidate_urls = [
            "http://172.18.0.1:3002/api/rebuild",
            "http://172.17.0.1:3002/api/rebuild",
            "http://host.docker.internal:3002/api/rebuild",
            "http://127.0.0.1:3002/api/rebuild",
        ]

        for url in candidate_urls:
            try:
                res = requests.post(url, headers=headers, timeout=60)
                if res.status_code == 200:
                    data = res.json()
                    logger.info("Site static rebuild completed via %s: %s", url, data)
                    return {"status": "SUCCESS", "details": data}
            except Exception as err:
                logger.debug("Rebuild attempt failed for %s: %s", url, err)

        logger.warning("Could not trigger site static rebuild (daemon unreachable).")
        return {"status": "SKIPPED", "message": "Rebuild daemon unreachable"}


site_publisher = SitePublisher()
