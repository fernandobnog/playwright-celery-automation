"""
Evolution API Client for WhatsApp Integration.
Provides robust methods for phone number normalization, verification, and text messaging.
"""

import logging
import re
from typing import Any, Dict, List, Optional
import httpx

from core.config import settings

logger = logging.getLogger(__name__)


def format_brazilian_phone(raw_phone: str) -> str:
    """
    Normalizes Brazilian phone numbers according to the standard rules:
    - Strips non-digits
    - Adds default DDD (19) if omitted (8 or 9 digits)
    - Strips leading zero if present
    - Prepend DDI 55 if missing
    - Validates DDD and length (12 or 13 digits)
    """
    if not raw_phone:
        raise ValueError("Phone number cannot be empty")

    digits = re.sub(r"\D", "", raw_phone)
    if not digits:
        raise ValueError("Phone number contains no digits")

    # If only local number (8 or 9 digits), assume DDD 19
    if len(digits) in (8, 9):
        digits = "19" + digits

    if digits.startswith("0"):
        digits = digits[1:]

    if not digits.startswith("55") and len(digits) < 12:
        digits = "55" + digits

    if len(digits) not in (12, 13):
        raise ValueError(
            f"Invalid phone length ({len(digits)} digits). Expected 12 or 13 digits: {digits}"
        )

    ddd = digits[2:4]
    if len(ddd) != 2:
        raise ValueError(f"Invalid DDD: {ddd}")

    number = digits[4:]
    if len(number) not in (8, 9):
        raise ValueError(f"Invalid subscriber number: {number}")

    return digits


class EvolutionClient:
    """
    Async client for Evolution API v2.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        instance: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ):
        self.base_url = (base_url or settings.EVOLUTION_API_URL).rstrip("/")
        self.api_key = api_key or settings.EVOLUTION_API_KEY
        self.instance = instance or settings.EVOLUTION_INSTANCE
        self.timeout = timeout_seconds

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }

    async def check_whatsapp_number(self, phone: str) -> bool:
        """
        Queries Evolution API to check if a phone number is registered on WhatsApp.
        """
        normalized = format_brazilian_phone(phone)
        url = f"{self.base_url}/chat/whatsappNumbers/{self.instance}"
        payload = {"numbers": [normalized]}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

            if isinstance(data, list) and len(data) > 0:
                return bool(data[0].get("exists", False))
            if isinstance(data, dict):
                return bool(data.get("exists", False))
            return False

    async def send_text_message(self, phone: str, text: str) -> Dict[str, Any]:
        """
        Sends a plain text WhatsApp message to the recipient.
        """
        normalized = format_brazilian_phone(phone)
        url = f"{self.base_url}/message/sendText/{self.instance}"
        payload = {
            "number": normalized,
            "text": text,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def send_media_message(
        self,
        phone: str,
        media_base64_or_url: str,
        file_name: str,
        caption: Optional[str] = None,
        media_type: str = "document",
        mime_type: str = "application/pdf",
    ) -> Dict[str, Any]:
        """
        Sends media/document (like PDF proposals) via WhatsApp using Evolution API.
        """
        normalized = format_brazilian_phone(phone)
        url = f"{self.base_url}/message/sendMedia/{self.instance}"
        payload: Dict[str, Any] = {
            "number": normalized,
            "media": media_base64_or_url,
            "mediatype": media_type,
            "mimetype": mime_type,
            "fileName": file_name,
        }
        if caption:
            payload["caption"] = caption

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=self.headers, json=payload)
            resp.raise_for_status()
            return resp.json()

