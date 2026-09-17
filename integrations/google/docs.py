"""
Google Docs API Service Wrapper.
Provides document creation, template instantiation, text placeholder replacement,
and programmatic content generation.
"""

import logging
from typing import Any, Dict, Optional
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
