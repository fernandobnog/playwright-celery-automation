"""
Gmail API Service Wrapper.
Provides robust methods for sending HTML emails, template rendering (Jinja2),
attachments handling, and message retrieval.
"""

import base64
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from googleapiclient.discovery import build
from jinja2 import Template

from integrations.google.auth import GoogleAuthManager

logger = logging.getLogger(__name__)


class GmailService:
    """
    Client for Gmail API operations.
    """

    def __init__(self, auth_manager: Optional[GoogleAuthManager] = None):
        self.auth = auth_manager or GoogleAuthManager()

    def _get_service(self):
        creds = self.auth.get_credentials("gmail")
        return build("gmail", "v1", credentials=creds)

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        from_email: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        thread_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Sends an email via Gmail API with optional attachments and CC/BCC.
        Attachments format: [{'filename': 'report.pdf', 'content': b'...', 'mimetype': 'application/pdf'}]
        """
        service = self._get_service()

        message = MIMEMultipart("mixed") if attachments else MIMEMultipart("alternative")
        message["to"] = to_email
        message["subject"] = subject
        if from_email:
            message["from"] = from_email
        if cc:
            message["cc"] = ", ".join(cc)
        if bcc:
            message["bcc"] = ", ".join(bcc)

        # Body container
        if attachments:
            body_part = MIMEMultipart("alternative")
            if text_body:
                body_part.attach(MIMEText(text_body, "plain", "utf-8"))
            body_part.attach(MIMEText(html_body, "html", "utf-8"))
            message.attach(body_part)

            # Attach files
            for att in attachments:
                filename = att.get("filename", "attachment")
                content = att.get("content", b"")
                mimetype = att.get("mimetype", "application/octet-stream")
                maintype, subtype = mimetype.split("/", 1) if "/" in mimetype else ("application", "octet-stream")

                part = MIMEApplication(content, _subtype=subtype)
                part.add_header("Content-Disposition", "attachment", filename=filename)
                message.attach(part)
        else:
            if text_body:
                message.attach(MIMEText(text_body, "plain", "utf-8"))
            message.attach(MIMEText(html_body, "html", "utf-8"))

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        send_body: Dict[str, Any] = {"raw": raw_message}
        if thread_id:
            send_body["threadId"] = thread_id

        logger.info("Dispatching email via Gmail API to %s: '%s'", to_email, subject)
        return (
            service.users()
            .messages()
            .send(userId="me", body=send_body)
            .execute()
        )

    def send_jinja_template(
        self,
        to_email: str,
        subject: str,
        template_source: Union[str, Path],
        context: Dict[str, Any],
        is_file: bool = True,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Renders a Jinja2 template and sends it.
        """
        if is_file:
            path = Path(template_source)
            if not path.exists():
                alt_path = Path("/app") / path
                if alt_path.exists():
                    path = alt_path
            with open(path, "r", encoding="utf-8") as f:
                template_str = f.read()
        else:
            template_str = str(template_source)

        tmpl = Template(template_str)
        rendered_html = tmpl.render(**context)
        return self.send_email(
            to_email=to_email,
            subject=subject,
            html_body=rendered_html,
            attachments=attachments,
        )

    def list_messages(self, query: str = "", max_results: int = 10) -> List[Dict[str, Any]]:
        """
        Queries messages matching search syntax (e.g. 'is:unread label:INBOX').
        """
        service = self._get_service()
        response = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        return response.get("messages", [])

    def get_message(self, message_id: str, fmt: str = "full") -> Dict[str, Any]:
        """
        Retrieves detailed metadata and payload of a specific message.
        """
        service = self._get_service()
        return (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format=fmt)
            .execute()
        )
