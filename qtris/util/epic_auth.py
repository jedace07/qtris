"""Sign in to Epic in the host's browser, with the Epic Games Launcher's client.

Epic's bot check turns embedded browsers away, and Google refuses to sign in inside
one. Epic Account Services' device authorization needs neither: the user approves a
code in their own browser while Qtris polls.

That gives an Epic Account Services session, which the launcher's APIs refuse. The
account service accepts its refresh token from the same client, though, and answers
with a launcher session. An exchange code from that signs legendary in.

Tokens stay out of the log, so failures log only an exception's type or Epic's error code.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from gettext import gettext as _
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import requests

from qtris.exceptions import QtrisError
from qtris.util.http import get_request_proxies
from qtris.util.log import logger

if TYPE_CHECKING:
    from collections.abc import Callable

# launcherAppClient2, the client legendary signs in with too
_CLIENT = ("34a02cf8f4414e29b15921876da36f9a", "daafbccc737745039dffe53d94fc76cf")
# Epic's older account service has a device flow too, but not for this client
_EAS_URL = "https://api.epicgames.dev/epic/oauth"
_ACCOUNT_URL = "https://account-public-service-prod03.ol.epicgames.com/account/api/oauth"
_TIMEOUT_SECONDS = 25


class EpicAuthError(QtrisError):
    """Epic didn't sign the user in."""


@dataclass(frozen=True)
class DeviceLogin:
    url: str
    user_code: str
    device_code: str = field(repr=False)
    interval: float = 11
    expires_in: float = 600


@dataclass(frozen=True)
class ApprovedSession:
    refresh_token: str = field(repr=False)


def start() -> DeviceLogin:
    """Ask Epic for a code the user can approve in their browser."""
    try:
        status, body = _send("POST", f"{_EAS_URL}/v1/deviceAuthorization", data={"client_id": _CLIENT[0]})
    except requests.RequestException as error:
        logger.warning("Could not reach Epic to sign in (%s)", type(error).__name__)
        raise EpicAuthError(_("Could not reach Epic Games. Check your connection and try again.")) from None
    try:
        url = str(body["verification_uri_complete"])
        address = urlsplit(url)
        # The frontend hands this address to the desktop to open
        if status != 200 or address.scheme != "https" or address.netloc != "www.epicgames.com":
            raise ValueError("Not an Epic Games activation page")
        return DeviceLogin(
            url,
            str(body["user_code"]),
            str(body["device_code"]),
            # Epic refuses a poll that comes any sooner than its interval
            float(body.get("interval", 10)) + 1,
            float(body.get("expires_in", 600)),
        )
    except KeyError, TypeError, ValueError:
        logger.warning("Epic did not start a sign-in (%s %s)", status, body.get("errorCode"))
        raise EpicAuthError(_("Epic Games could not start a sign-in. Try again later.")) from None


async def wait_for_approval(login: DeviceLogin) -> ApprovedSession:
    """Poll until the user approves the code. Cancelling stops polling, and revokes
    an approval that arrives meanwhile."""
    interval = login.interval
    deadline = time.monotonic() + login.expires_in
    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        poll = asyncio.create_task(asyncio.to_thread(_poll, login))
        try:
            result = await asyncio.shield(poll)
        except asyncio.CancelledError:
            late = await poll
            if isinstance(late, ApprovedSession):
                await asyncio.to_thread(revoke, late)
            raise
        if isinstance(result, ApprovedSession):
            return result
        if result == "slow_down":
            interval += 5
        elif result == "refused":
            raise EpicAuthError(_("The sign-in was declined or its code expired. Try again."))
    raise EpicAuthError(_("The sign-in code expired. Try again."))


def _poll(login: DeviceLogin) -> ApprovedSession | Literal["waiting", "slow_down", "refused"]:
    data = {"grant_type": "device_code", "device_code": login.device_code}
    try:
        status, body = _send("POST", f"{_EAS_URL}/v1/token", data=data)
    except requests.RequestException as error:
        # The user has minutes to approve, so a dropped connection needn't end the sign-in
        logger.debug("Could not poll Epic's sign-in (%s)", type(error).__name__)
        return "waiting"
    if status == 200 and body.get("refresh_token"):
        return ApprovedSession(str(body["refresh_token"]))
    error_code = str(body.get("errorCode", ""))
    if error_code.endswith("slow_down") or status == 429:
        return "slow_down"
    if error_code.endswith("authorization_pending") or status >= 500:
        return "waiting"
    logger.warning("Epic ended the sign-in (%s %s)", status, error_code)
    return "refused"


def hand_over(session: ApprovedSession, accept: Callable[[str], None]) -> None:
    """Turn the approved session into a launcher session and give `accept` an exchange
    code from it. The launcher session is revoked only if that fails: revoking it
    afterwards could end the session made from the code."""
    data = {"grant_type": "refresh_token", "refresh_token": session.refresh_token, "token_type": "eg1"}
    try:
        status, body = _send("POST", f"{_ACCOUNT_URL}/token", data=data)
    except requests.RequestException as error:
        logger.warning("Could not reach Epic to finish signing in (%s)", type(error).__name__)
        revoke(session)
        raise EpicAuthError(_("Could not reach Epic Games. Check your connection and try again.")) from None
    access_token = str(body.get("access_token") or "")
    if status != 200 or not access_token:
        error_code = str(body.get("errorCode", ""))
        logger.warning("Epic did not give a launcher session (%s %s)", status, error_code)
        revoke(session)
        if error_code.endswith("corrective_action_required"):
            raise EpicAuthError(
                _("Epic Games needs you to review your account on its website. Do that, then try again.")
            )
        raise EpicAuthError(_("Epic Games did not finish the sign-in. Try again."))

    accepted = False
    try:
        status, body = _send("GET", f"{_ACCOUNT_URL}/exchange", token=access_token)
        if status != 200 or not body.get("code"):
            logger.warning("Epic did not give an exchange code (%s %s)", status, body.get("errorCode"))
            raise EpicAuthError(_("Epic Games did not finish the sign-in. Try again."))
        accept(str(body["code"]))
        accepted = True
    except requests.RequestException as error:
        logger.warning("Could not get an exchange code from Epic (%s)", type(error).__name__)
        raise EpicAuthError(_("Could not reach Epic Games. Check your connection and try again.")) from None
    finally:
        if not accepted:
            _send_quietly("DELETE", f"{_ACCOUNT_URL}/sessions/kill/{access_token}", token=access_token)


def revoke(session: ApprovedSession) -> None:
    """End an approved session that won't be used."""
    _send_quietly("POST", f"{_EAS_URL}/v2/revoke", data={"token": session.refresh_token})


def _send_quietly(method: str, url: str, *, token: str = "", data: dict[str, str] | None = None) -> None:
    try:
        _send(method, url, token=token, data=data)
    except requests.RequestException as error:
        logger.debug("Could not revoke an Epic session (%s)", type(error).__name__)


def _send(method: str, url: str, *, token: str = "", data: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    # Not qtris.util.http.Request, which drops the error bodies that polling reads
    response = requests.request(
        method,
        url,
        data=data,
        auth=None if token else _CLIENT,
        headers={"Authorization": f"bearer {token}"} if token else None,
        timeout=_TIMEOUT_SECONDS,
        proxies=get_request_proxies(url),
        allow_redirects=False,
    )
    try:
        body = response.json()
    except ValueError:
        body = None
    return response.status_code, body if isinstance(body, dict) else {}
