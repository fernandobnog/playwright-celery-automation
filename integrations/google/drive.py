"""
Google Drive API Service Wrapper.
Provides file copying, template cloning, PDF exportation, folder management,
and file sharing capabilities.
"""

import io
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from integrations.google.auth import GoogleAuthManager

logger = logging.getLogger(__name__)


class DriveService:
    """
    Client for Google Drive API operations.
    """

    def __init__(self, auth_manager: Optional[GoogleAuthManager] = None):
        self.auth = auth_manager or GoogleAuthManager()

    def _get_service(self):
        creds = self.auth.get_credentials("drive")
        return build("drive", "v3", credentials=creds)

    def copy_file(
        self,
        file_id: str,
        new_title: str,
        destination_folder_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Clones an existing file (like a Doc template) to create an instance.
        """
        service = self._get_service()
        body: Dict[str, Any] = {"name": new_title}
        if destination_folder_id:
            body["parents"] = [destination_folder_id]

        copied_file = service.files().copy(fileId=file_id, body=body).execute()
        logger.info(
            "Copied Drive file %s to '%s' (New ID: %s)",
            file_id,
            new_title,
            copied_file.get("id"),
        )
        return copied_file

    def export_as_pdf(
        self,
        file_id: str,
        output_file_path: Optional[str] = None,
    ) -> bytes:
        """
        Exports a Google Document or Spreadsheet directly to PDF format.
        """
        service = self._get_service()
        request = service.files().export_media(fileId=file_id, mimeType="application/pdf")

        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()

        pdf_bytes = fh.getvalue()

        if output_file_path:
            out_p = Path(output_file_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            with open(out_p, "wb") as f:
                f.write(pdf_bytes)
            logger.info("Saved exported PDF to %s (%d bytes)", output_file_path, len(pdf_bytes))

        return pdf_bytes

    def create_folder(
        self,
        folder_name: str,
        parent_folder_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Creates a new folder in Google Drive.
        """
        service = self._get_service()
        file_metadata: Dict[str, Any] = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_folder_id:
            file_metadata["parents"] = [parent_folder_id]

        folder = service.files().create(body=file_metadata, fields="id, name").execute()
        logger.info("Created Drive folder '%s' (ID: %s)", folder_name, folder.get("id"))
        return folder

    def upload_file(
        self,
        local_path: str,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        parent_folder_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Uploads a local file into Google Drive.
        """
        service = self._get_service()
        p = Path(local_path)
        name = filename or p.name
        mtype = mime_type or "application/octet-stream"

        file_metadata: Dict[str, Any] = {"name": name}
        if parent_folder_id:
            file_metadata["parents"] = [parent_folder_id]

        media = MediaFileUpload(str(p), mimetype=mtype)
        uploaded = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id, name, webViewLink")
            .execute()
        )
        logger.info("Uploaded file %s to Drive (ID: %s)", name, uploaded.get("id"))
        return uploaded

    def list_files(
        self,
        query: Optional[str] = None,
        page_size: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Searches for files matching Google Drive query syntax.
        """
        service = self._get_service()
        q = query or "trashed = false"
        results = (
            service.files()
            .list(q=q, pageSize=page_size, fields="files(id, name, mimeType, webViewLink)")
            .execute()
        )
        return results.get("files", [])

    def share_file(
        self,
        file_id: str,
        role: str = "reader",
        permission_type: str = "anyone",
    ) -> Dict[str, Any]:
        """
        Configures sharing permission on a file or folder.
        """
        service = self._get_service()
        permission = {
            "type": permission_type,
            "role": role,
        }
        return service.permissions().create(fileId=file_id, body=permission).execute()
