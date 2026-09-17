"""
Twenty CRM REST API Client.
Provides typed async methods to manage People, Notes, and NoteTargets.
"""

import logging
from typing import Any, Dict, Optional
import httpx

from core.config import settings

logger = logging.getLogger(__name__)


class TwentyCRMClient:
    """
    Async client for Twenty CRM REST API.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: float = 12.0,
    ):
        self.base_url = (base_url or settings.TWENTY_CRM_URL).rstrip("/")
        self.token = token or settings.TWENTY_CRM_TOKEN
        self.timeout = timeout_seconds

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def find_person_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """
        Searches for an existing contact by primary email address.
        """
        if not email:
            return None

        url = f"{self.base_url}/people"
        params = {"filter": f"emails.primaryEmail[eq]:{email.strip()}"}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers, params=params)
            resp.raise_for_status()
            data = resp.json()

            people = data.get("data", {}).get("people", [])
            if people and len(people) > 0:
                return people[0]
            return None

    async def create_person(
        self,
        first_name: str,
        last_name: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> str:
        """
        Creates a new person record in Twenty CRM and returns its UUID.
        """
        url = f"{self.base_url}/people"
        payload: Dict[str, Any] = {
            "name": {
                "firstName": first_name.strip(),
                "lastName": last_name.strip(),
            },
            "emails": {
                "primaryEmail": email.strip() if email else None,
            },
            "phones": {
                "primaryPhoneNumber": phone.strip() if phone else "",
                "primaryPhoneCallingCode": "+55",
                "primaryPhoneCountryCode": "BR",
            },
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            person_id = (
                data.get("data", {}).get("id")
                or data.get("data", {}).get("createPerson", {}).get("id")
                or data.get("id")
            )
            if not person_id:
                raise ValueError(f"Could not extract person ID from response: {data}")
            return person_id

    async def create_note(self, title: str, markdown_body: str) -> str:
        """
        Creates a note record in Twenty CRM.
        """
        url = f"{self.base_url}/notes"
        payload = {
            "title": title,
            "bodyV2": {
                "markdown": markdown_body or "",
            },
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            note_id = (
                data.get("data", {}).get("id")
                or data.get("data", {}).get("createNote", {}).get("id")
                or data.get("id")
            )
            if not note_id:
                raise ValueError(f"Could not extract note ID from response: {data}")
            return note_id

    async def link_note_to_person(self, note_id: str, person_id: str) -> Dict[str, Any]:
        """
        Associates a note to a person via NoteTargets.
        """
        url = f"{self.base_url}/noteTargets"
        payload = {
            "noteId": note_id,
            "targetPersonId": person_id,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            resp.raise_for_status()
            return resp.json()
