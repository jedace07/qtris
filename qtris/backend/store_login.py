"""Keep store sign-in handoffs and credentials inside the backend.

GOG and Amazon sign in in a web view the frontend shows: their sign-in ends on
fixed store pages that only an embedded browser can watch. The Epic Games Store
signs in in the host browser and hands the copied authorization code to Legendary.
The other stores sign in in the host browser and hand back a redirect address,
a cookie export or a key.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from gettext import gettext as _
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from qtris.exceptions import QtrisError
from qtris.services import SERVICES
from qtris.services.amazon import AmazonService
from qtris.services.base import OnlineService
from qtris.services.egs import EpicGamesStoreService
from qtris.services.gog import GOGService
from qtris.services.itchio import ItchIoService
from qtris.services.ubisoft import UbisoftConnectService
from qtris.services.zoom import ZoomService
from qtris.util import itchio_oauth
from qtris.util.cookies import load_cookie_file, make_cookie, select_cookies
from qtris.util.log import logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    from requests.cookies import RequestsCookieJar

type LoginInput = Literal["web", "epic_code", "redirect", "api_key", "itch_oauth", "cookies", "ubisoft"]

_EPIC_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,255}")
_MAX_LOGIN_RESPONSE = 16384
_REDIRECTS = {
    "gog": (("https://embed.gog.com/on_login_success", "https://www.gog.com/on_login_success"), "code"),
    "amazon": (("https://www.amazon.com/", "https://amazon.com/"), "openid.oa2.authorization_code"),
    "zoomplatform": (("https://www.zoom-platform.com/account",), "li_token"),
}
_WEB_LOGINS = frozenset({"gog", "amazon"})
_COOKIE_DOMAINS = {
    "humblebundle": "humblebundle.com",
    "ea_app": "ea.com",
    "gamejolt": "gamejolt.com",
    "dlsite": "dlsite.com",
}


@dataclass(frozen=True)
class BrowserLogin:
    id: str
    name: str
    url: str
    input_kind: LoginInput


@dataclass(frozen=True)
class WebCookie:
    """A cookie the sign-in web view received."""

    domain: str
    name: str
    value: str
    path: str = "/"
    secure: bool = False
    http_only: bool = False
    # Seconds since the epoch; None for a session cookie
    expires: int | None = None


@dataclass
class _PendingLogin:
    request: BrowserLogin
    service: OnlineService
    service_id: str
    working: bool = False
    itch_oauth: itchio_oauth.ItchOAuth | None = None


_pending: dict[str, _PendingLogin] = {}
_starting: set[str] = set()


def login_input(service_id: str) -> LoginInput | None:
    if service_id in _WEB_LOGINS:
        return "web"
    if service_id == "egs":
        return "epic_code"
    if service_id in _REDIRECTS:
        return "redirect"
    if service_id in _COOKIE_DOMAINS:
        return "cookies"
    if service_id == "itchio":
        return "itch_oauth" if os.environ.get("QTRIS_ITCHIO_CLIENT_ID", "").strip() else "api_key"
    if service_id == "ubisoft":
        return "ubisoft"
    return None


async def begin(service_id: str, *, use_api_key: bool = False) -> BrowserLogin:
    """Create a sign-in request. Keep its store instance for the callback."""
    input_kind = "api_key" if service_id == "itchio" and use_api_key else login_input(service_id)
    service_class = SERVICES.get(service_id)
    if not input_kind or not service_class or not issubclass(service_class, OnlineService):
        raise QtrisError(_("Browser sign-in is unavailable for this store."))
    if service_id in _starting or any(item.service_id == service_id for item in _pending.values()):
        raise QtrisError(_("A sign-in for this store is already open."))

    _starting.add(service_id)
    try:
        service = service_class()
        itch_oauth = None
        if isinstance(service, EpicGamesStoreService):
            url = service.begin_sign_in()
        elif input_kind == "itch_oauth":
            itch_oauth = await itchio_oauth.begin(os.environ["QTRIS_ITCHIO_CLIENT_ID"].strip())
            url = itch_oauth.url
        elif input_kind == "api_key":
            url = "https://itch.io/user/settings/api-keys"
        else:
            # Amazon's address comes from Nile, which registers a new device for it
            url = await asyncio.to_thread(lambda: str(service.login_url))
        request = BrowserLogin(uuid4().hex, str(service.name), url, input_kind)
        _pending[request.id] = _PendingLogin(request, service, service_id, itch_oauth=itch_oauth)
        return request
    finally:
        _starting.discard(service_id)


def is_web_response(request_id: str, url: str) -> bool:
    """Say whether an address the sign-in web view reached is the response to pass to
    finish(). Plain parsing, so the frontend can ask while the page loads."""
    pending = _pending.get(request_id)
    if not pending or pending.request.input_kind != "web":
        return False
    try:
        _redirect_response(pending.service_id, url)
    except QtrisError:
        return False
    return True


def epic_authorization_code(value: str, *, allow_plain: bool = False) -> str:
    """Read Epic's JSON response or a code the user submitted explicitly."""
    if len(value) > _MAX_LOGIN_RESPONSE:
        return ""
    value = value.strip()
    if value.startswith("{"):
        try:
            payload = json.loads(value)
        except ValueError, RecursionError:
            return ""
        code = payload.get("authorizationCode")
        return code if isinstance(code, str) and _EPIC_CODE.fullmatch(code) else ""
    if allow_plain:
        code = value.strip('"')
        return code if _EPIC_CODE.fullmatch(code) else ""
    return ""


async def cancel(request_id: str) -> None:
    pending = _pending.get(request_id)
    if pending and not pending.working:
        _pending.pop(request_id)
        if pending.itch_oauth:
            pending.itch_oauth.close()


async def wait_for_itchio(request_id: str) -> str:
    pending = _pending.get(request_id)
    if not pending or not pending.itch_oauth:
        raise QtrisError(_("This sign-in request is no longer available."))
    return await pending.itch_oauth.wait()


async def finish(request_id: str, value: str = "", cookies: Sequence[WebCookie] = ()) -> None:
    """Validate and exchange the user's sign-in response without logging it.
    A web sign-in passes the cookies its web view received."""
    pending = _pending.get(request_id)
    if not pending or pending.working:
        raise QtrisError(_("This sign-in request is no longer available."))
    pending.working = True
    try:
        task = asyncio.create_task(asyncio.to_thread(_finish, pending, value.strip(), tuple(cookies)))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            finally:
                _pending.pop(request_id, None)
            raise
    finally:
        pending.working = False
    _pending.pop(request_id, None)


def _finish(pending: _PendingLogin, value: str, cookies: tuple[WebCookie, ...]) -> None:
    service = pending.service
    if not value or len(value) > _MAX_LOGIN_RESPONSE:
        raise QtrisError(_("Enter the sign-in response from your browser."))
    if pending.request.input_kind in ("redirect", "web"):
        value = _redirect_response(pending.service_id, value)
    elif pending.request.input_kind == "api_key" and any(character.isspace() for character in value):
        raise QtrisError(_("Enter a single API key from itch.io."))

    try:
        service.is_login_in_progress = False
        if pending.request.input_kind == "cookies":
            service.import_cookies(_load_store_cookies(Path(value), _COOKIE_DOMAINS[pending.service_id]))
        elif isinstance(service, GOGService):
            _sign_in_to_gog(service, value, cookies)
        elif isinstance(service, EpicGamesStoreService):
            code = epic_authorization_code(value, allow_plain=True)
            if not code:
                raise QtrisError(_("Copy the sign-in response or authorization code from Epic Games."))
            service.sign_in(code)
        elif isinstance(service, ItchIoService):
            if pending.itch_oauth:
                service.save_oauth_token(pending.itch_oauth.exchange(value))
            else:
                service.sign_in_with_api_key(value)
        elif isinstance(service, AmazonService | ZoomService):
            service.login_callback(value)
        elif isinstance(service, UbisoftConnectService):
            service.import_browser_session(_ubisoft_session(value))
        if not service.is_authenticated():
            raise QtrisError(_("The store did not accept the sign-in response."))
    except QtrisError:
        raise
    except Exception as error:
        logger.warning("Browser sign-in failed for %s (%s)", service.id, type(error).__name__)
        raise QtrisError(_("The store did not accept the sign-in. Try again.")) from None


def _ubisoft_session(value: str) -> dict[str, str]:
    try:
        payload = json.loads(value)
    except ValueError:
        payload = None
    session: dict[str, str] = {}
    for entry in payload if isinstance(payload, list) else [payload]:
        if not isinstance(entry, dict):
            continue
        for key in ("ticket", "sessionId", "userId", "rememberMeTicket"):
            candidate = entry.get(key)
            if isinstance(candidate, str) and candidate and candidate.isascii() and candidate.isprintable():
                if not any(character.isspace() for character in candidate):
                    session[key] = candidate
        name = entry.get("nameOnPlatform")
        if isinstance(name, str) and name.isprintable():
            session["username"] = name
    if not {"ticket", "sessionId", "userId"} <= session.keys():
        raise QtrisError(_("Paste the Ubisoft Connect login data as JSON."))
    return session


def _redirect_response(service_id: str, value: str) -> str:
    targets, key = _REDIRECTS[service_id]
    try:
        url = urlsplit(value)
        target = f"{url.scheme}://{url.netloc.lower()}{url.path or '/'}"
        parameters = parse_qs(url.query)
        response = parameters.get(key, [])
        if target in targets and len(response) == 1 and not any(char.isspace() for char in response[0]):
            return f"{target}?{urlencode({key: response[0]})}"
    except ValueError:
        pass
    raise QtrisError(_("Paste the full sign-in redirect URL from the store."))


def _sign_in_to_gog(service: GOGService, redirect: str, cookies: tuple[WebCookie, ...]) -> None:
    # GOG's library endpoint takes the web session's cookies as well as the API token
    jar = select_cookies(
        (
            make_cookie(
                cookie.domain,
                cookie.name,
                cookie.value,
                path=cookie.path or "/",
                secure=cookie.secure,
                http_only=cookie.http_only,
                expires=cookie.expires,
            )
            for cookie in cookies
        ),
        "gog.com",
    )
    service.login_callback(redirect, cookies=jar)


def _load_store_cookies(source: Path, domain: str) -> RequestsCookieJar:
    try:
        if not source.is_file():
            raise ValueError("Expected a cookie export file")
        cookies = load_cookie_file(source, max_size=2_000_000)
    except OSError, ValueError:
        raise QtrisError(_("Select a JSON or Netscape cookie export from your browser.")) from None
    selected = select_cookies(cookies, domain)
    if not selected:
        raise QtrisError(_("The file has no current cookies for %s.") % domain)
    return selected
