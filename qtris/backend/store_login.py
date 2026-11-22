"""Keep store sign-in handoffs and credentials inside the backend.

GOG and Amazon sign in in a web view the frontend shows: their sign-in ends on
fixed store pages that only an embedded browser can watch. The Epic Games Store
signs in in the host browser, where the user approves a code while Qtris waits.
The other stores sign in in the host browser and hand back a redirect address,
a cookie export or a key.
"""

import asyncio
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from gettext import gettext as _
from http.cookiejar import Cookie
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

from qtris.exceptions import QtrisError
from qtris.services import SERVICES
from qtris.services.amazon import AmazonService
from qtris.services.base import OnlineService
from qtris.services.ea_app import EAAppService
from qtris.services.egs import EpicGamesStoreService
from qtris.services.gog import GOGService
from qtris.services.itchio import ItchIoService
from qtris.services.ubisoft import UbisoftConnectService
from qtris.services.zoom import ZoomService
from qtris.util import epic_auth
from qtris.util.cookies import WebkitCookieJar
from qtris.util.log import logger

type LoginInput = Literal["web", "device", "redirect", "api_key", "cookies", "ubisoft"]

_REDIRECTS = {
    "gog": (("https://embed.gog.com/on_login_success", "https://www.gog.com/on_login_success"), "code"),
    "amazon": (("https://www.amazon.com/", "https://amazon.com/"), "openid.oa2.authorization_code"),
    "zoomplatform": (("https://www.zoom-platform.com/account",), "li_token"),
}
_WEB_LOGINS = frozenset({"gog", "amazon"})
# GOG's library endpoint takes the web session's cookies as well as the API token
_WEB_COOKIE_DOMAINS = {"gog": "gog.com"}
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
    # What the user approves in the browser, for a device sign-in
    user_code: str = ""


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
    device: epic_auth.DeviceLogin | None = None
    approval: epic_auth.ApprovedSession | None = None


_pending: dict[str, _PendingLogin] = {}


def login_input(service_id: str) -> LoginInput | None:
    if service_id in _WEB_LOGINS:
        return "web"
    if service_id == "egs":
        return "device"
    if service_id in _REDIRECTS:
        return "redirect"
    if service_id in _COOKIE_DOMAINS:
        return "cookies"
    if service_id == "itchio":
        return "api_key"
    if service_id == "ubisoft":
        return "ubisoft"
    return None


async def begin(service_id: str) -> BrowserLogin:
    """Create a sign-in request. Keep its store instance for the callback."""
    input_kind = login_input(service_id)
    service_class = SERVICES.get(service_id)
    if not input_kind or not service_class or not issubclass(service_class, OnlineService):
        raise QtrisError(_("Browser sign-in is unavailable for this store."))
    if any(item.service_id == service_id for item in _pending.values()):
        raise QtrisError(_("A sign-in for this store is already open."))

    service = service_class()
    device = None
    if isinstance(service, EpicGamesStoreService):
        device = await asyncio.to_thread(service.begin_sign_in)
        url = device.url
    elif input_kind == "api_key":
        url = "https://itch.io/user/settings/api-keys"
    else:
        # Amazon's address comes from Nile, which registers a new device for it
        url = await asyncio.to_thread(lambda: str(service.login_url))
    user_code = device.user_code if device else ""
    request = BrowserLogin(uuid4().hex, str(service.name), url, input_kind, user_code)
    _pending[request.id] = _PendingLogin(request, service, service_id, device=device)
    return request


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


async def wait_for_approval(request_id: str) -> None:
    """Return once the user has approved a device sign-in in their browser; finish()
    then signs in. Cancelling this stops waiting."""
    pending = _pending.get(request_id)
    if not pending or not pending.device:
        raise QtrisError(_("This sign-in request is no longer available."))
    session = await epic_auth.wait_for_approval(pending.device)
    if request_id not in _pending:
        await asyncio.to_thread(epic_auth.revoke, session)
        raise QtrisError(_("This sign-in request is no longer available."))
    pending.approval = session


async def cancel(request_id: str) -> None:
    pending = _pending.get(request_id)
    if pending and not pending.working:
        _pending.pop(request_id)
        if pending.approval:
            await asyncio.to_thread(epic_auth.revoke, pending.approval)


async def finish(request_id: str, value: str = "", cookies: Sequence[WebCookie] = ()) -> None:
    """Validate and exchange the user's sign-in response without logging it.
    A web sign-in passes the cookies its web view received. A device sign-in has no
    response; wait_for_approval() got it."""
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
    approval, pending.approval = pending.approval, None
    if pending.request.input_kind == "device":
        if not approval:
            raise QtrisError(_("Approve the sign-in in your browser first."))
    elif not value or len(value) > 16384:
        raise QtrisError(_("Enter the sign-in response from your browser."))
    if pending.request.input_kind in ("redirect", "web"):
        value = _redirect_response(pending.service_id, value)
    elif pending.request.input_kind == "api_key" and any(character.isspace() for character in value):
        raise QtrisError(_("Enter a single API key from itch.io."))

    try:
        service.is_login_in_progress = False
        if pending.request.input_kind == "cookies":
            _store_cookies(service, _load_cookie_file(Path(value)), _COOKIE_DOMAINS[pending.service_id])
        elif isinstance(service, GOGService):
            _sign_in_to_gog(service, value, cookies)
        elif isinstance(service, EpicGamesStoreService) and approval:
            service.sign_in(approval)
        elif isinstance(service, ItchIoService):
            if not service.validate_api_key(value):
                raise QtrisError(_("itch.io did not accept that API key."))
            service.save_api_key(value)
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
    jar = WebkitCookieJar()
    for cookie in cookies:
        jar.set_cookie(_to_cookie(cookie))
    if _select_cookies(jar, _WEB_COOKIE_DOMAINS["gog"]):
        _store_cookies(service, jar, _WEB_COOKIE_DOMAINS["gog"], gog_redirect=redirect)
        return
    # The API token is enough for GOG; a cookie file from an earlier sign-in is stale
    Path(service.cookies_path).unlink(missing_ok=True)
    service.login_callback(redirect)


def _to_cookie(cookie: WebCookie) -> Cookie:
    domain = cookie.domain.lower()
    return Cookie(
        version=0,
        name=cookie.name,
        value=cookie.value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=domain.startswith("."),
        domain_initial_dot=domain.startswith("."),
        path=cookie.path or "/",
        path_specified=True,
        secure=cookie.secure,
        expires=cookie.expires,
        discard=cookie.expires is None,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": ""} if cookie.http_only else {},
    )


def _load_cookie_file(source: Path) -> WebkitCookieJar:
    try:
        if not source.is_file() or source.stat().st_size > 2_000_000:
            raise ValueError("Not a small cookie file")
        cookies = WebkitCookieJar(str(source))
        cookies.load(ignore_discard=True)
    except OSError, ValueError:
        raise QtrisError(_("Select a Netscape-format cookies.txt export from the browser.")) from None
    return cookies


def _select_cookies(cookies: WebkitCookieJar, domain: str) -> WebkitCookieJar:
    selected = WebkitCookieJar()
    for cookie in cookies:
        cookie_domain = cookie.domain.lstrip(".").lower()
        if cookie_domain == domain or cookie_domain.endswith(f".{domain}"):
            selected.set_cookie(cookie)
    return selected


def _store_cookies(service: OnlineService, cookies: WebkitCookieJar, domain: str, *, gog_redirect: str = "") -> None:
    selected = _select_cookies(cookies, domain)
    if not len(selected):
        raise QtrisError(_("The file has no current cookies for %s.") % domain)

    if not isinstance(service.cookies_path, str):
        raise QtrisError(_("This store does not accept a cookie file."))
    target = Path(service.cookies_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent) as folder:
        staged = Path(folder) / "cookies.txt"
        selected.save(str(staged), ignore_discard=True)
        staged.chmod(0o600)
        service.cookies_path = str(staged)
        try:
            if isinstance(service, EAAppService):
                service.fetch_access_token()
            elif isinstance(service, GOGService):
                service.login_callback(gog_redirect)
                if not service.is_authenticated():
                    raise QtrisError(_("GOG did not accept the sign-in response."))
            elif not service.is_connected():
                raise QtrisError(_("The store did not accept those cookies. Sign in again and export new cookies."))
        finally:
            service.cookies_path = str(target)
        os.replace(staged, target)
