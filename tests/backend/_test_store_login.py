"""Exercise browser handoffs without using real account credentials."""

import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from qtris import settings
from qtris.backend import services, store_login
from qtris.exceptions import QtrisError
from qtris.services.gog import GOGService
from qtris.services.humblebundle import HumbleBundleService
from qtris.services.ubisoft import UbisoftConnectService
from qtris.util import epic_auth
from qtris.util.credentials import save_credentials
from qtris.util.test_config import setup_test_environment
from qtris.util.ubisoft.client import UbisoftConnectClient
from tests.store_clients import install_store_clients

setup_test_environment()


def test_gog_signs_in_from_its_web_view_and_keeps_only_gog_cookies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / ".gog.token"
    cookie_path = tmp_path / ".gog.auth"
    responses: list[str] = []

    def exchange(self: GOGService, url: str, refresh_token: str = "") -> None:
        responses.append(url)
        save_credentials(self.token_path, "{}")

    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(GOGService, "token_path", str(token))
    monkeypatch.setattr(GOGService, "cookies_path", str(cookie_path))
    monkeypatch.setattr(GOGService, "request_token", exchange)
    redirect = "https://embed.gog.com/on_login_success?origin=client&code=fixture"
    cookies = (
        store_login.WebCookie(".gog.com", "gog-al", "session", secure=True, http_only=True, expires=4_000_000_000),
        store_login.WebCookie("login.gog.com", "GalaxyLogin", "fixture"),
        store_login.WebCookie(".gog.com.example", "stolen", "no"),
    )

    async def run() -> None:
        request = await store_login.begin("gog")
        assert request.input_kind == "web" and urlsplit(request.url).netloc == "auth.gog.com"
        for page in (
            request.url,
            "https://login.gog.com/login",
            "https://embed.gog.com@other.example/on_login_success?code=secret",
            "https://embed.gog.com/on_login_success?code=one&code=two",
        ):
            assert not store_login.is_web_response(request.id, page)
            with pytest.raises(QtrisError, match="full sign-in redirect URL"):
                await store_login.finish(request.id, page)
        assert store_login.is_web_response(request.id, redirect)
        assert not responses
        await store_login.finish(request.id, redirect, cookies)
        with pytest.raises(QtrisError, match="no longer available"):
            await store_login.finish(request.id, redirect)
        assert not store_login.is_web_response(request.id, redirect)
        assert (await services.get_source_status("gog")).signed_in

    asyncio.run(run())
    assert responses == ["https://embed.gog.com/on_login_success?code=fixture"]
    assert token.stat().st_mode & 0o777 == 0o600
    saved = cookie_path.read_text(encoding="utf-8")
    assert "gog-al" in saved and "GalaxyLogin" in saved and "stolen" not in saved
    assert cookie_path.stat().st_mode & 0o777 == 0o600


def test_gog_signs_in_with_the_token_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = tmp_path / ".gog.token"
    cookie_path = tmp_path / ".gog.auth"
    cookie_path.write_text(".gog.com\tTRUE\t/\tTRUE\t0\tstale\tsession\n", encoding="utf-8")

    def exchange(self: GOGService, url: str, refresh_token: str = "") -> None:
        save_credentials(self.token_path, "{}")

    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(GOGService, "token_path", str(token))
    monkeypatch.setattr(GOGService, "cookies_path", str(cookie_path))
    monkeypatch.setattr(GOGService, "request_token", exchange)

    async def run() -> None:
        request = await store_login.begin("gog")
        await store_login.finish(request.id, "https://embed.gog.com/on_login_success?code=fixture")
        assert (await services.get_source_status("gog")).signed_in

    asyncio.run(run())
    assert not cookie_path.exists()
    assert GOGService().load_cookies() is None


def test_amazon_signs_in_through_nile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    install_store_clients(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))

    async def run() -> None:
        request = await store_login.begin("amazon")
        assert request.input_kind == "web"
        assert "openid.oa2.code_challenge" in parse_qs(urlsplit(request.url).query)
        assert not store_login.is_web_response(request.id, request.url)
        assert not store_login.is_web_response(request.id, "https://www.amazon.com/ap/signin?openid.mode=x")
        assert store_login.is_web_response(
            request.id, "https://www.amazon.com/?openid.oa2.authorization_code=amazon-code"
        )
        with pytest.raises(QtrisError, match="full sign-in redirect URL"):
            await store_login.finish(request.id, "https://www.amazon.com.example/?openid.oa2.authorization_code=x")
        await store_login.finish(request.id, "https://www.amazon.com/?openid.oa2.authorization_code=amazon-code")
        assert (await services.get_source_status("amazon")).signed_in

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())
    # Nile prints the tokens it gets; they must never reach the log
    assert "SECRET-TOKEN" not in caplog.text


def test_epic_signs_in_through_the_browser_and_legendary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_store_clients(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    session = epic_auth.ApprovedSession("refresh")
    device = epic_auth.DeviceLogin("https://www.epicgames.com/activate?userCode=CODE", "CODE", "device")
    codes = ["wrong-code", "epic-code"]

    async def approve(login: epic_auth.DeviceLogin) -> epic_auth.ApprovedSession:
        assert login == device
        return session

    monkeypatch.setattr(epic_auth, "start", lambda: device)
    monkeypatch.setattr(epic_auth, "wait_for_approval", approve)
    monkeypatch.setattr(epic_auth, "hand_over", lambda approved, accept: accept(codes.pop(0)))

    async def run() -> None:
        request = await store_login.begin("egs")
        assert (request.input_kind, request.user_code, request.url) == ("device", "CODE", device.url)
        assert not store_login.is_web_response(request.id, request.url)
        with pytest.raises(QtrisError, match="Approve the sign-in"):
            await store_login.finish(request.id)
        await store_login.wait_for_approval(request.id)
        # legendary reports a refused code only in its output, with exit status 0
        with pytest.raises(QtrisError, match="legendary could not sign in"):
            await store_login.finish(request.id)
        assert not (await services.get_source_status("egs")).signed_in
        # An approval is good for one try
        with pytest.raises(QtrisError, match="Approve the sign-in"):
            await store_login.finish(request.id)
        await store_login.wait_for_approval(request.id)
        await store_login.finish(request.id)
        assert (await services.get_source_status("egs")).signed_in
        await services.disconnect_source("egs")
        assert not (await services.get_source_status("egs")).signed_in

    asyncio.run(run())


def test_cancelling_an_approved_epic_sign_in_revokes_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_store_clients(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    session = epic_auth.ApprovedSession("refresh")
    revoked: list[epic_auth.ApprovedSession] = []

    async def approve(login: epic_auth.DeviceLogin) -> epic_auth.ApprovedSession:
        return session

    monkeypatch.setattr(epic_auth, "start", lambda: epic_auth.DeviceLogin("https://epic.test", "CODE", "device"))
    monkeypatch.setattr(epic_auth, "wait_for_approval", approve)
    monkeypatch.setattr(epic_auth, "revoke", revoked.append)

    async def run() -> None:
        request = await store_login.begin("egs")
        await store_login.wait_for_approval(request.id)
        await store_login.cancel(request.id)

    asyncio.run(run())
    assert revoked == [session]


def test_epic_checks_for_legendary_before_opening_the_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_DIR", str(tmp_path))
    started: list[bool] = []
    monkeypatch.setattr(epic_auth, "start", lambda: started.append(True))
    with pytest.raises(QtrisError, match="hasn't been downloaded"):
        asyncio.run(store_login.begin("egs"))
    assert not started


def test_cookie_import_only_persists_the_selected_stores_cookies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / ".humblebundle.auth"
    source = tmp_path / "cookies.txt"
    source.write_text(
        "# Netscape HTTP Cookie File\n"
        "#HttpOnly_.humblebundle.com\tTRUE\t/\tTRUE\t0\tsession\thumble-session\n"
        ".other.example\tTRUE\t/\tTRUE\t0\tsession\tunrelated-session\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(HumbleBundleService, "cookies_path", str(target))

    async def run() -> None:
        request = await store_login.begin("humblebundle")
        await store_login.finish(request.id, str(source))
        assert (await services.get_source_status("humblebundle")).signed_in

    asyncio.run(run())
    cookies = HumbleBundleService().load_cookies()
    assert cookies is not None
    assert [(cookie.domain, cookie.name) for cookie in cookies] == [(".humblebundle.com", "session")]
    assert "unrelated-session" not in target.read_text(encoding="utf-8")
    assert target.stat().st_mode & 0o777 == 0o600


def test_ubisoft_combines_storage_values_and_only_saves_verified_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / ".ubisoft.token"
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(UbisoftConnectService, "token_path", str(token))

    def verify(self: UbisoftConnectClient, method: str, url: str, **kwargs: object) -> dict[str, str]:
        assert method == "post" and url.endswith("/v2/profiles/sessions")
        assert kwargs["timeout"] == 15
        return {"userId": "account", "nameOnPlatform": "Player"}

    monkeypatch.setattr(UbisoftConnectClient, "_do_request", verify)

    async def run() -> None:
        request = await store_login.begin("ubisoft")
        login = {"ticket": "fixture-ticket", "sessionId": "fixture-session"}
        with pytest.raises(QtrisError, match="login data as JSON"):
            await store_login.finish(request.id, json.dumps(login))
        with pytest.raises(QtrisError, match="did not accept"):
            await store_login.finish(request.id, json.dumps(login | {"userId": "other"}))
        assert not token.exists()
        await store_login.finish(
            request.id, json.dumps([login, {"rememberMeTicket": "fixture-refresh"}, {"userId": "account"}])
        )
        assert (await services.get_source_status("ubisoft")).signed_in

    asyncio.run(run())
    saved = json.loads(token.read_text(encoding="utf-8"))
    assert saved["userId"] == "account" and saved["username"] == "Player"
    assert saved["rememberMeTicket"] == "fixture-refresh"
    assert token.stat().st_mode & 0o777 == 0o600
