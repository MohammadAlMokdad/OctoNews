"""WhatsApp gateway clients against a fake HTTP server (no network)."""

import json

import httpx
import pytest

from octonews.config import Settings
from octonews.whatsapp import GatewayError, Waha, Whapi, make_sender

CHANNEL = "120363412923413789@newsletter"


def waha_with(handler):
    return Waha("http://localhost:3000/", "secret-key", "default", transport=httpx.MockTransport(handler))


def test_waha_send_text_request_shape_and_id():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["key"] = request.headers.get("X-Api-Key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": {"_serialized": "true_120@newsletter_ABC"}})

    assert waha_with(handler).send_text(CHANNEL, "مرحبا *hi*") == "true_120@newsletter_ABC"
    assert seen == {
        "path": "/api/sendText",
        "key": "secret-key",
        "body": {"session": "default", "chatId": CHANNEL, "text": "مرحبا *hi*"},
    }


def test_waha_send_error_raises():
    wa = waha_with(lambda r: httpx.Response(422, json={"message": "Session status is not as expected"}))
    with pytest.raises(GatewayError, match="422"):
        wa.send_text(CHANNEL, "x")


def test_rejects_non_channel_id():
    with pytest.raises(GatewayError, match="@newsletter"):
        waha_with(lambda r: httpx.Response(200, json={})).send_text("123@c.us", "x")


def test_waha_list_channels_and_status():
    def handler(request: httpx.Request):
        if request.url.path == "/api/default/channels":
            return httpx.Response(200, json=[{"id": CHANNEL, "name": "OctoNews", "role": "ADMIN"}])
        if request.url.path == "/api/sessions/default":
            return httpx.Response(200, json={"name": "default", "status": "WORKING"})
        return httpx.Response(404)

    wa = waha_with(handler)
    assert wa.list_channels()[0]["name"] == "OctoNews"
    assert wa.status() == "WORKING"


def test_make_sender_picks_provider():
    assert isinstance(make_sender(Settings(feeds=[], whatsapp_provider="waha")), Waha)
    assert isinstance(make_sender(Settings(feeds=[], whatsapp_provider="whapi", whapi_token="t")), Whapi)
    with pytest.raises(RuntimeError, match="WHATSAPP_PROVIDER"):
        make_sender(Settings(feeds=[], whatsapp_provider="carrier-pigeon"))
