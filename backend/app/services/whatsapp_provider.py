"""WhatsApp Provider Abstraction & Official Cloud API Adapter.

Implements Phase 5.1 of Master Specification:
- Provider abstraction decoupling business logic from messaging transports
- Official Meta WhatsApp Cloud API provider
- MockWhatsAppProvider fallback for CI and local development (TEST_MODE=true)
"""
from __future__ import annotations

import hmac
import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Any
import httpx

from app.config import settings

logger = logging.getLogger("whatsapp_provider")


class BaseWhatsAppProvider(ABC):
    """Abstract interface for all WhatsApp message providers."""

    @abstractmethod
    async def send_message(self, to: str, message: str) -> dict[str, Any]:
        """Send a WhatsApp text message."""
        pass

    @abstractmethod
    def verify_webhook(self, raw_body: bytes, signature_header: str) -> bool:
        """Verify webhook signature."""
        pass


class WhatsAppCloudAPIProvider(BaseWhatsAppProvider):
    """Official Meta WhatsApp Cloud API (Graph API v18.0+)."""

    def __init__(self, phone_number_id: str, access_token: str, app_secret: str | None = None):
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        self.app_secret = app_secret
        self.base_url = f"https://graph.facebook.com/v18.0/{self.phone_number_id}/messages"

    async def send_message(self, to: str, message: str) -> dict[str, Any]:
        # Normalize recipient phone number (remove + and spaces)
        clean_to = to.replace("+", "").replace(" ", "").replace("-", "")
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_to,
            "type": "text",
            "text": {"preview_url": False, "body": message},
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.base_url, headers=headers, json=payload)
                if resp.status_code in [200, 201]:
                    data = resp.json()
                    msg_id = data.get("messages", [{}])[0].get("id", "cloud_msg_id")
                    logger.info("WhatsApp Cloud API sent message to %s (ID: %s)", clean_to, msg_id)
                    return {"success": True, "provider": "CLOUD_API", "message_id": msg_id}
                else:
                    logger.error("WhatsApp Cloud API failed (%d): %s", resp.status_code, resp.text)
                    return {"success": False, "error": resp.text, "status_code": resp.status_code}
        except Exception as e:
            logger.error("WhatsApp Cloud API connection error: %s", e)
            return {"success": False, "error": str(e)}

    def verify_webhook(self, raw_body: bytes, signature_header: str) -> bool:
        if not self.app_secret or not signature_header:
            return False
        # Header format: sha256=HEX_DIGEST
        if not signature_header.startswith("sha256="):
            return False
        expected_sig = signature_header.split("sha256=", 1)[1]
        computed_sig = hmac.new(
            self.app_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected_sig, computed_sig)


class MockWhatsAppProvider(BaseWhatsAppProvider):
    """Deterministic in-memory mock for tests and offline development."""

    def __init__(self):
        self.sent_messages: list[dict[str, Any]] = []

    async def send_message(self, to: str, message: str) -> dict[str, Any]:
        record = {
            "success": True,
            "provider": "MOCK",
            "to": to,
            "message": message,
            "message_id": f"mock_wa_{len(self.sent_messages) + 1}",
        }
        self.sent_messages.append(record)
        logger.info("MockWhatsAppProvider: Simulated message to %s: %s", to, message[:60])
        return record

    def verify_webhook(self, raw_body: bytes, signature_header: str) -> bool:
        return signature_header == "mock_valid_signature"


def get_whatsapp_provider() -> BaseWhatsAppProvider:
    """Return active provider based on environment and available credentials."""
    if settings.TEST_MODE or not getattr(settings, "WHATSAPP_PHONE_NUMBER_ID", None) or not getattr(settings, "WHATSAPP_ACCESS_TOKEN", None):
        return MockWhatsAppProvider()

    return WhatsAppCloudAPIProvider(
        phone_number_id=settings.WHATSAPP_PHONE_NUMBER_ID,
        access_token=settings.WHATSAPP_ACCESS_TOKEN,
        app_secret=getattr(settings, "WHATSAPP_APP_SECRET", None),
    )
