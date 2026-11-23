from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from qtris import settings
from qtris.backend import store_login
from qtris.services.itchio import ItchIoService
from qtris.util import itchio_oauth

if TYPE_CHECKING:
    from pathlib import Path


async def callback(port: int, path: str, **parameters: str) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path}?{urlencode(parameters)} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii"))
    await writer.drain()
    reply = await reader.read()
    writer.close()
    await writer.wait_closed()
    return reply


def test_itchio_browser_login_exchanges_only_the_matching_callback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QTRIS_ITCHIO_CLIENT_ID", "fixture-app")
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    request_data: dict[str, str] = {}

    class TokenResponse:
        status_code = 200

        def json(self) -> dict[str, str]:
            return {"access_token": "fixture-token", "scope": "profile:me profile:owned game:view:uploads"}

    def exchange(url: str, *, data: dict[str, str], **kwargs: Any) -> TokenResponse:
        assert url == "https://api.itch.io/oauth/token"
        assert kwargs["allow_redirects"] is False
        request_data.update(data)
        return TokenResponse()

    monkeypatch.setattr(itchio_oauth.requests, "post", exchange)

    async def run() -> None:
        request = await store_login.begin("itchio")
        assert request.input_kind == "itch_oauth"
        authorization = urlsplit(request.url)
        assert authorization.netloc == "itch.io"
        query = parse_qs(authorization.query)
        uri, state, challenge = (query[field][0] for field in ("redirect_uri", "state", "code_challenge"))
        redirect = urlsplit(uri)
        port = redirect.port
        assert redirect.hostname == "127.0.0.1" and port is not None
        assert "code_verifier" not in query
        assert b"400 Bad Request" in await callback(port, redirect.path, state="wrong-state", code="bad-code")
        assert b"400 Bad Request" in await callback(port, "/other", state=state, code="bad-code")
        assert b"200 OK" in await callback(port, redirect.path, state=state, code="good-code")
        code = await store_login.wait_for_itchio(request.id)
        await store_login.finish(request.id, code)
        expected_challenge = base64.urlsafe_b64encode(hashlib.sha256(request_data["code_verifier"].encode()).digest())
        assert expected_challenge.decode().rstrip("=") == challenge
        assert request_data["code"] == "good-code" and request_data["redirect_uri"] == uri
        assert request_data["client_id"] == "fixture-app"
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", port)

    asyncio.run(run())
    assert ItchIoService().get_headers() == {"Authorization": "Bearer fixture-token"}


def test_itchio_cancel_closes_browser_callback_and_allows_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QTRIS_ITCHIO_CLIENT_ID", "fixture-app")

    async def run() -> None:
        request = await store_login.begin("itchio")
        redirect = urlsplit(parse_qs(urlsplit(request.url).query)["redirect_uri"][0])
        port = redirect.port
        assert port is not None
        await store_login.cancel(request.id)
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", port)
        fallback = await store_login.begin("itchio", use_api_key=True)
        assert fallback.input_kind == "api_key"
        await store_login.cancel(fallback.id)

    asyncio.run(run())
