"""Whapi.Cloud client: the only module that talks to WhatsApp.

Whapi is an unofficial gateway (Meta has no official Channels API). Each call below costs one
request from the Whapi Sandbox quota (1,000/month), so the bot only calls it when actually posting.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://gate.whapi.cloud"


class WhapiError(RuntimeError):
    pass


class WhatsApp:
    def __init__(self, token: str, timeout: float = 30.0):
        if not token:
            raise RuntimeError("WHAPI_TOKEN is not set")
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=timeout,
        )

    def send_text(self, channel_id: str, body: str) -> str:
        """Post a text message to a channel (`...@newsletter`). Returns the message id."""
        if not channel_id.endswith("@newsletter"):
            raise WhapiError(f"Channel id should end with @newsletter, got: {channel_id!r}")
        resp = self._client.post("/messages/text", json={"to": channel_id, "body": body})
        data = _json(resp)
        if resp.status_code >= 400 or data.get("sent") is False:
            raise WhapiError(f"Whapi send failed ({resp.status_code}): {str(data)[:300]}")
        return str((data.get("message") or {}).get("id", ""))

    def list_channels(self) -> list[dict]:
        resp = self._client.get("/newsletters")
        data = _json(resp)
        if resp.status_code >= 400:
            raise WhapiError(f"Whapi list failed ({resp.status_code}): {str(data)[:300]}")
        for key in ("newsletters", "data"):  # dict response or bare list (wrapped by _json)
            if isinstance(data.get(key), list):
                return data[key]
        return []

    def close(self) -> None:
        self._client.close()


def _json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {"raw": resp.text[:300]}
