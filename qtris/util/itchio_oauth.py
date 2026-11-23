"""Receive itch.io's browser authorization code over a PKCE-protected loopback callback."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from gettext import gettext as _
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from qtris.exceptions import QtrisError
from qtris.util.http import get_request_proxies

_CALLBACK_PATH = "/callback"
_SCOPES = ("profile:me", "profile:owned", "game:view:uploads")
_TOKEN_URL = "https://api.itch.io/oauth/token"


@dataclass(frozen=True)
class ItchOAuth:
    url: str
    client_id: str
    redirect_uri: str
    verifier: str = field(repr=False)
    server: asyncio.Server
    response: asyncio.Future[str | None] = field(repr=False)

    async def wait(self) -> str:
        try:
            async with asyncio.timeout(600):
                code = await self.response
        except TimeoutError:
            raise QtrisError(_("itch.io sign-in timed out. Start again.")) from None
        finally:
            self.close()
        if code is None:
            raise QtrisError(_("itch.io sign-in was declined."))
        return code

    def close(self) -> None:
        self.server.close()
        self.response.cancel()

    def exchange(self, code: str) -> str:
        try:
            response = requests.post(
                _TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.client_id,
                    "redirect_uri": self.redirect_uri,
                    "code_verifier": self.verifier,
                    "code": code,
                },
                proxies=get_request_proxies(_TOKEN_URL),
                allow_redirects=False,
                timeout=30,
            )
            if response.status_code == 200:
                payload = response.json()
                if isinstance(payload, dict):
                    token = payload.get("access_token")
                    scope = payload.get("scope")
                    if (
                        isinstance(token, str)
                        and token
                        and isinstance(scope, str)
                        and set(_SCOPES) <= set(scope.split())
                    ):
                        return token
        except requests.RequestException, ValueError:
            raise QtrisError(_("Could not exchange the itch.io sign-in response. Start again.")) from None
        raise QtrisError(_("itch.io did not grant access to your library. Start again."))


async def begin(client_id: str) -> ItchOAuth:
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(32)
    response: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            async with asyncio.timeout(3):
                headers = await reader.readuntil(b"\r\n\r\n")
                method, target, _version = headers.split(b"\r\n", 1)[0].decode("ascii").split()
            url = urlsplit(target)
            query = parse_qs(url.query)
            ok = (
                method == "GET"
                and not url.netloc
                and not url.scheme
                and url.path == _CALLBACK_PATH
                and query.get("state") == [state]
                and not response.done()
            )
            code = query.get("code", [])
            if ok and len(code) == 1 and "error" not in query and not any(char.isspace() for char in code[0]):
                response.set_result(code[0])
                message = _("Sign-in response received. You can return to Qtris.")
            elif ok and query.get("error") == ["access_denied"]:
                response.set_result(None)
                message = _("Sign-in declined. You can return to Qtris.")
            else:
                ok = False
                message = _("Invalid sign-in response.")
            status = b"200 OK" if ok else b"400 Bad Request"
            writer.write(
                b"HTTP/1.1 " + status + b"\r\nContent-Type: text/plain; charset=utf-8\r\n"
                b"Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nConnection: close\r\n\r\n"
                + message.encode("utf-8")
            )
            await writer.drain()
        except TimeoutError, OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError:
            return
        finally:
            writer.close()

    try:
        server = await asyncio.start_server(handle, "127.0.0.1", 0, limit=8192)
    except OSError:
        raise QtrisError(_("Could not start the local itch.io sign-in callback.")) from None
    port = server.sockets[0].getsockname()[1]
    redirect_uri = f"http://127.0.0.1:{port}{_CALLBACK_PATH}"
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(_SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    return ItchOAuth(f"https://itch.io/user/oauth?{query}", client_id, redirect_uri, verifier, server, response)
