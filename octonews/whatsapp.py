"""WhatsApp gateways: the only module that talks to WhatsApp.

Meta has no official API for posting to Channels, so the bot drives a linked WhatsApp account through
a gateway. Two are supported, behind the same small interface (send_text / list_channels / close):

- WAHA  (default): free, open-source, self-hosted next to the bot. https://waha.devlike.pro
- Whapi.Cloud:     hosted service (its free Sandbox is limited to 1,000 requests/month).
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

WHAPI_BASE_URL = "https://gate.whapi.cloud"


class GatewayError(RuntimeError):
    pass


WhapiError = GatewayError  # backwards-compatible name


def _check_channel_id(channel_id: str) -> None:
    if not channel_id.endswith("@newsletter"):
        raise GatewayError(f"Channel id should end with @newsletter, got: {channel_id!r}")


def _json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text[:300]}


class Waha:
    """Self-hosted WAHA (WhatsApp HTTP API), normally at http://localhost:3000 on the same server."""

    def __init__(self, base_url: str, api_key: str, session: str = "default", timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None):
        headers = {"Accept": "application/json"}
        if api_key:
            headers["X-Api-Key"] = api_key
        self.session = session
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout, transport=transport
        )

    def send_text(self, channel_id: str, body: str) -> str:
        """Post a text message to a channel (`...@newsletter`). Returns the message id."""
        _check_channel_id(channel_id)
        resp = self._client.post(
            "/api/sendText", json={"session": self.session, "chatId": channel_id, "text": body}
        )
        data = _json(resp)
        if resp.status_code >= 400:
            raise GatewayError(f"WAHA send failed ({resp.status_code}): {str(data)[:300]}")
        msg_id = data.get("id") if isinstance(data, dict) else None
        if isinstance(msg_id, dict):  # some engines return a structured id
            msg_id = msg_id.get("_serialized") or msg_id.get("id")
        return str(msg_id or "")

    def list_channels(self) -> list[dict]:
        resp = self._client.get(f"/api/{self.session}/channels")
        data = _json(resp)
        if resp.status_code >= 400:
            raise GatewayError(f"WAHA list failed ({resp.status_code}): {str(data)[:300]}")
        return data if isinstance(data, list) else []

    def status(self) -> str:
        """Session status, e.g. WORKING, SCAN_QR_CODE, STOPPED, FAILED."""
        resp = self._client.get(f"/api/sessions/{self.session}")
        data = _json(resp)
        if resp.status_code >= 400:
            raise GatewayError(f"WAHA status failed ({resp.status_code}): {str(data)[:300]}")
        return str(data.get("status", "UNKNOWN")) if isinstance(data, dict) else "UNKNOWN"

    def close(self) -> None:
        self._client.close()


class Whapi:
    """Whapi.Cloud hosted gateway."""

    def __init__(self, token: str, timeout: float = 30.0):
        if not token:
            raise RuntimeError("WHAPI_TOKEN is not set")
        self._client = httpx.Client(
            base_url=WHAPI_BASE_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
        )

    def send_text(self, channel_id: str, body: str) -> str:
        _check_channel_id(channel_id)
        resp = self._client.post("/messages/text", json={"to": channel_id, "body": body})
        data = _json(resp)
        data = data if isinstance(data, dict) else {"data": data}
        if resp.status_code >= 400 or data.get("sent") is False:
            raise GatewayError(f"Whapi send failed ({resp.status_code}): {str(data)[:300]}")
        return str((data.get("message") or {}).get("id", ""))

    def list_channels(self) -> list[dict]:
        resp = self._client.get("/newsletters")
        data = _json(resp)
        if resp.status_code >= 400:
            raise GatewayError(f"Whapi list failed ({resp.status_code}): {str(data)[:300]}")
        if isinstance(data, list):
            return data
        items = data.get("newsletters") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    def close(self) -> None:
        self._client.close()


def make_sender(settings):
    """Build the gateway client chosen by WHATSAPP_PROVIDER (waha | whapi)."""
    if settings.whatsapp_provider == "whapi":
        return Whapi(settings.whapi_token)
    if settings.whatsapp_provider == "waha":
        return Waha(settings.waha_url, settings.waha_api_key, settings.waha_session)
    raise RuntimeError(f"Unknown WHATSAPP_PROVIDER: {settings.whatsapp_provider!r} (use waha or whapi)")
