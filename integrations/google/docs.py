"""
Google Docs API Service Wrapper.
Provides document creation, template instantiation, text placeholder replacement,
and programmatic content generation.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.request
from googleapiclient.discovery import build

from integrations.google.auth import GoogleAuthManager

logger = logging.getLogger(__name__)


class DocsService:
    """
    Client for Google Docs API operations.
    """

    def __init__(self, auth_manager: Optional[GoogleAuthManager] = None):
        self.auth = auth_manager or GoogleAuthManager()

    def _get_service(self):
        creds = self.auth.get_credentials("docs")
        return build("docs", "v1", credentials=creds)

    def create_document(self, title: str) -> Dict[str, Any]:
        """
        Creates a new blank Google Document and returns its metadata including documentId.
        """
        service = self._get_service()
        body = {"title": title}
        doc = service.documents().create(body=body).execute()
        logger.info("Created new Google Document '%s' (ID: %s)", title, doc.get("documentId"))
        return doc

    def get_document(self, document_id: str) -> Dict[str, Any]:
        """
        Retrieves the full structural content of a Google Document.
        """
        service = self._get_service()
        return service.documents().get(documentId=document_id).execute()

    def extract_text_content(self, document_id: str) -> str:
        """
        Retrieves the Google Document and extracts its complete plain text content,
        preserving paragraph breaks, table cell text, and headings.
        """
        doc = self.get_document(document_id)
        return self._extract_text_from_elements(doc.get("body", {}).get("content", []))

    def _extract_text_from_elements(self, elements: list) -> str:
        text_parts = []
        for element in elements:
            if "paragraph" in element:
                p_elements = element.get("paragraph", {}).get("elements", [])
                for pe in p_elements:
                    text_run = pe.get("textRun")
                    if text_run and "content" in text_run:
                        text_parts.append(text_run["content"])
            elif "table" in element:
                for row in element.get("table", {}).get("tableRows", []):
                    row_parts = []
                    for cell in row.get("tableCells", []):
                        cell_text = self._extract_text_from_elements(cell.get("content", [])).strip()
                        row_parts.append(cell_text)
                    text_parts.append(" | ".join(row_parts) + "\n")
            elif "tableOfContents" in element:
                toc_elements = element.get("tableOfContents", {}).get("content", [])
                text_parts.append(self._extract_text_from_elements(toc_elements))
        return "".join(text_parts)


    def replace_text(
        self,
        document_id: str,
        replacements: Dict[str, str],
        match_case: bool = True,
    ) -> Dict[str, Any]:
        """
        Performs batch text replacements across the entire document.
        Automatically checks both {{key}} and raw key placeholders.
        """
        service = self._get_service()
        requests = []

        for key, value in replacements.items():
            str_val = str(value) if value is not None else ""

            # 1. Match with mustache brackets {{key}}
            pattern_mustache = f"{{{{{key}}}}}" if not key.startswith("{{") else key
            requests.append({
                "replaceAllText": {
                    "containsText": {
                        "text": pattern_mustache,
                        "matchCase": match_case,
                    },
                    "replaceText": str_val,
                }
            })

            # 2. Also match raw key if it doesn't have mustache
            if not key.startswith("{{"):
                requests.append({
                    "replaceAllText": {
                        "containsText": {
                            "text": key,
                            "matchCase": match_case,
                        },
                        "replaceText": str_val,
                    }
                })

        if not requests:
            return {"replies": []}

        body = {"requests": requests}
        result = service.documents().batchUpdate(documentId=document_id, body=body).execute()
        logger.info(
            "Executed %d text replacements in document %s",
            len(requests),
            document_id,
        )
        return result

    def append_text(self, document_id: str, text: str) -> Dict[str, Any]:
        """
        Appends text to the end of the Google Document.
        """
        service = self._get_service()
        doc = self.get_document(document_id)
        # Find the end index of the document body
        body_content = doc.get("body", {}).get("content", [])
        end_index = body_content[-1].get("endIndex", 1) - 1 if body_content else 1

        requests = [
            {
                "insertText": {
                    "location": {"index": max(1, end_index)},
                    "text": text if text.endswith("\n") else text + "\n",
                }
            }
        ]
        return service.documents().batchUpdate(
            documentId=document_id, body={"requests": requests}
        ).execute()

    def download_first_inline_image(self, document_id: str, dest_path: str) -> Optional[str]:
        """
        Extracts the first inline image inserted in the Google Document (e.g. user pasted cover)
        and saves it to dest_path. Returns dest_path if successfully downloaded, or None.
        """
        try:
            doc = self.get_document(document_id)
            inline_objects = doc.get("inlineObjects", {})
            if not inline_objects:
                logger.debug("No inlineObjects found in Google Doc %s", document_id)
                return None

            ordered_ids: List[str] = []
            for element in doc.get("body", {}).get("content", []):
                if "paragraph" in element:
                    for pe in element.get("paragraph", {}).get("elements", []):
                        inline_ref = pe.get("inlineObjectElement", {}).get("inlineObjectId")
                        if inline_ref and inline_ref in inline_objects:
                            ordered_ids.append(inline_ref)

            target_ids = ordered_ids if ordered_ids else list(inline_objects.keys())
            for obj_id in target_ids:
                embedded_obj = inline_objects[obj_id].get("inlineObjectProperties", {}).get("embeddedObject", {})
                img_props = embedded_obj.get("imageProperties", {})
                content_uri = img_props.get("contentUri")
                if content_uri:
                    dest = Path(dest_path)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    import httpx
                    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
                    resp = httpx.get(content_uri, headers=headers, timeout=40.0, follow_redirects=True)
                    if resp.status_code == 200 and len(resp.content) > 100:
                        dest.write_bytes(resp.content)
                        logger.info("Successfully downloaded inline cover image from Doc %s to %s (%d bytes)", document_id, dest, len(resp.content))
                        return str(dest)
                    else:
                        logger.warning("Failed to fetch image from contentUri: HTTP %s", resp.status_code)
        except Exception as exc:
            logger.warning("Error attempting to download inline image from Doc %s: %s", document_id, exc)

        return None

