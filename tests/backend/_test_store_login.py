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
from qtris.services.gamejolt import GameJoltService
from qtris.services.gog import GOGService
from qtris.services.humblebundle import HumbleBundleService
from qtris.services.ubisoft import UbisoftConnectService
from qtris.util.http import Request
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

    def exchange(self: Request) -> Request:
        responses.append(urlsplit(self.url).query)
        self.content = b'{"access_token":"access","refresh_token":"refresh"}'
        return self

    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(GOGService, "token_path", str(token))
    monkeypatch.setattr(GOGService, "cookies_path", str(cookie_path))
    monkeypatch.setattr(Request, "get", exchange)
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
    assert len(responses) == 1 and "code=fixture" in responses[0]
    path = GOGService().credentials.path
    assert path.stat().st_mode & 0o777 == 0o600
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["token"]["access_token"] == "access"
    saved = {entry["name"]: entry for entry in document["cookies"]}
    assert set(saved) == {"gog-al", "GalaxyLogin"}
    assert saved["gog-al"]["httpOnly"] and saved["gog-al"]["expirationDate"] == 4_000_000_000
    assert saved["GalaxyLogin"]["hostOnly"] and saved["GalaxyLogin"]["session"]
    assert not cookie_path.exists() and not token.exists()


def test_gog_signs_in_with_the_token_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = tmp_path / ".gog.token"
    cookie_path = tmp_path / ".gog.auth"
    cookie_path.write_text(".gog.com\tTRUE\t/\tTRUE\t0\tstale\tsession\n", encoding="utf-8")

    def exchange(self: Request) -> Request:
        self.content = b'{"access_token":"access","refresh_token":"refresh"}'
        return self

    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(GOGService, "token_path", str(token))
    monkeypatch.setattr(GOGService, "cookies_path", str(cookie_path))
    monkeypatch.setattr(Request, "get", exchange)

    legacy = GOGService().load_cookies()
    assert legacy is not None and [(cookie.name, cookie.value) for cookie in legacy] == [("stale", "session")]

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
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))

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


@pytest.mark.parametrize("response", ['{"authorizationCode": "epic-code"}', "epic-code"])
def test_epic_signs_in_through_the_browser_and_legendary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: str
) -> None:
    install_store_clients(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))

    async def run() -> None:
        request = await store_login.begin("egs")
        assert (request.input_kind, request.url) == ("epic_code", "https://legendary.gl/epiclogin")
        assert not store_login.is_web_response(request.id, request.url)
        with pytest.raises(QtrisError, match="Copy the sign-in response"):
            await store_login.finish(request.id, '{"authorizationCode": "--delete"}')
        # Legendary reports a refused code only in its output, with exit status 0
        with pytest.raises(QtrisError, match="legendary could not sign in"):
            await store_login.finish(request.id, '{"authorizationCode": "wrong-code"}')
        assert not (await services.get_source_status("egs")).signed_in
        await store_login.finish(request.id, response)
        assert (await services.get_source_status("egs")).signed_in
        await services.disconnect_source("egs")
        assert not (await services.get_source_status("egs")).signed_in

    asyncio.run(run())


def test_cancelled_epic_sign_in_does_not_accept_a_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install_store_clients(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))

    async def run() -> None:
        request = await store_login.begin("egs")
        await store_login.cancel(request.id)
        with pytest.raises(QtrisError, match="no longer available"):
            await store_login.finish(request.id, "epic-code")
        retry = await store_login.begin("egs")
        await store_login.cancel(retry.id)

    asyncio.run(run())


def test_epic_checks_for_legendary_before_opening_the_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RUNTIME_DIR", str(tmp_path))
    with pytest.raises(QtrisError, match="hasn't been downloaded"):
        asyncio.run(store_login.begin("egs"))


@pytest.mark.parametrize("format_name", ["json", "wrapped", "netscape"])
def test_cookie_import_only_persists_the_selected_stores_cookies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, format_name: str
) -> None:
    target = tmp_path / ".humblebundle.auth"
    if format_name == "netscape":
        source = tmp_path / "cookies.txt"
        source.write_text(
            "# Netscape HTTP Cookie File\n"
            "#HttpOnly_.humblebundle.com\tTRUE\t/library\tTRUE\t4000000000\tsession\thumble-session\n"
            ".other.example\tTRUE\t/\tTRUE\t0\tsession\tunrelated-session\n"
            ".humblebundle.com\tTRUE\t/\tTRUE\t1\texpired\told\n",
            encoding="utf-8",
        )
    else:
        source = tmp_path / "cookies.json"
        records = [
            {
                "domain": "humblebundle.com",
                "hostOnly": False,
                "name": "session",
                "value": "humble-session",
                "path": "/library",
                "secure": True,
                "httpOnly": True,
                "expirationDate": 4_000_000_000.5,
            },
            {"domain": ".humblebundle.com.example", "name": "session", "value": "unrelated-session"},
            {"domain": ".humblebundle.com", "name": "expired", "value": "old", "expirationDate": 1},
            {
                "domain": ".humblebundle.com",
                "name": "partitioned",
                "value": "scoped",
                "partitionKey": {"topLevelSite": "https://other.example"},
            },
        ]
        source.write_text(json.dumps({"cookies": records} if format_name == "wrapped" else records), encoding="utf-8")
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(HumbleBundleService, "cookies_path", str(target))

    async def run() -> None:
        request = await store_login.begin("humblebundle")
        await store_login.finish(request.id, str(source))
        assert (await services.get_source_status("humblebundle")).signed_in

    asyncio.run(run())
    cookies = HumbleBundleService().load_cookies()
    assert cookies is not None
    (cookie,) = cookies
    assert (cookie.domain, cookie.name, cookie.value) == (".humblebundle.com", "session", "humble-session")
    assert cookie.secure and cookie.has_nonstandard_attr("HttpOnly")
    assert cookie.path == "/library" and cookie.expires == 4_000_000_000
    path = HumbleBundleService().credentials.path
    (saved,) = json.loads(path.read_text(encoding="utf-8"))["cookies"]
    assert saved["name"] == "session" and saved["httpOnly"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert not target.exists()


def test_cookie_import_keeps_saved_session_if_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / ".gamejolt.auth"
    legacy = ".gamejolt.com\tTRUE\t/\tTRUE\t0\tsession\tprevious\n"
    target.write_text(legacy, encoding="utf-8")
    source = tmp_path / "cookies.json"

    def is_connected(service: GameJoltService) -> bool:
        jar = service.load_cookies()
        assert jar is not None
        return any(cookie.value == "accepted" for cookie in jar)

    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(GameJoltService, "cookies_path", str(target))
    monkeypatch.setattr(GameJoltService, "is_connected", is_connected)

    async def run() -> None:
        request = await store_login.begin("gamejolt")
        source.write_text('{"cookies": [', encoding="utf-8")
        with pytest.raises(QtrisError, match="Select a JSON or Netscape cookie export"):
            await store_login.finish(request.id, str(source))
        source.write_text('[{"domain": "gamejolt.com", "name": "session", "value": "rejected"}]', encoding="utf-8")
        with pytest.raises(QtrisError, match="did not accept those cookies"):
            await store_login.finish(request.id, str(source))
        assert target.read_text(encoding="utf-8") == legacy
        source.write_text('[{"domain": "gamejolt.com", "name": "session", "value": "accepted"}]', encoding="utf-8")
        await store_login.finish(request.id, str(source))

    asyncio.run(run())
    assert not target.exists()
    (saved,) = GameJoltService().credentials.read()["cookies"]
    assert saved["hostOnly"] and saved["session"] and saved["value"] == "accepted"


def test_ubisoft_combines_storage_values_and_only_saves_verified_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / ".ubisoft.token"
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
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
    path = UbisoftConnectService().credentials.path
    saved = json.loads(path.read_text(encoding="utf-8"))["token"]
    assert saved["userId"] == "account" and saved["username"] == "Player"
    assert saved["rememberMeTicket"] == "fixture-refresh"
    assert path.stat().st_mode & 0o777 == 0o600
    assert not token.exists()
