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
from qtris.services.ea_app import EAAppService
from qtris.services.gog import GOGService
from qtris.services.ubisoft import UbisoftConnectService
from qtris.services.zoom import ZoomService
from qtris.util.http import Request, UnauthorizedAccessError
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
            with pytest.raises(QtrisError, match="valid sign-in redirect URL"):
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
        with pytest.raises(QtrisError, match="valid sign-in redirect URL"):
            await store_login.finish(request.id, "https://www.amazon.com.example/?openid.oa2.authorization_code=x")
        await store_login.finish(request.id, "https://www.amazon.com/?openid.oa2.authorization_code=amazon-code")
        assert (await services.get_source_status("amazon")).signed_in

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())
    # Nile prints the tokens it gets; they must never reach the log
    assert "SECRET-TOKEN" not in caplog.text


def test_zoom_captures_the_web_redirect_and_validates_before_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(ZoomService, "token_path", str(tmp_path / ".zoom.token"))
    credentials = ZoomService().credentials
    credentials.update(token="previous")

    def validate(request: Request) -> Request:
        assert request.url == "https://www.zoom-platform.com/li/loggedin"
        assert credentials.read()["token"] == "previous"
        if request.headers["Authorization"] != "Bearer accepted":
            raise UnauthorizedAccessError("Rejected by ZOOM")
        request.content = b'{"name": "Account"}'
        return request

    monkeypatch.setattr(Request, "get", validate)

    async def run() -> None:
        request = await store_login.begin("zoomplatform")
        assert request.input_kind == "web" and "return_li_token=true" in request.url
        account_url = "https://www.zoom-platform.com/account"
        for invalid in (
            account_url,
            f"{account_url}?li_token=one&li_token=two",
            "https://www.zoom-platform.com.example/account?li_token=accepted",
        ):
            assert not store_login.is_web_response(request.id, invalid, before_navigation=True)

        with pytest.raises(QtrisError, match="ZOOM rejected this session"):
            await store_login.finish(request.id, f"{account_url}?li_token=rejected")
        assert credentials.read()["token"] == "previous"
        redirect = f"{account_url}?li_token=accepted"
        assert store_login.is_web_response(request.id, redirect, before_navigation=True)
        assert store_login.is_web_response(request.id, redirect)
        await store_login.finish(request.id, redirect)
        assert not store_login.is_web_response(request.id, redirect)

    asyncio.run(run())
    assert credentials.read() == {"token": "accepted"}
    assert credentials.path.stat().st_mode & 0o777 == 0o600


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
    target = tmp_path / ".ea-cookies"
    if format_name == "netscape":
        source = tmp_path / "cookies.txt"
        source.write_text(
            "# Netscape HTTP Cookie File\n"
            "#HttpOnly_.ea.com\tTRUE\t/library\tTRUE\t4000000000\tsession\tea-session\n"
            ".other.example\tTRUE\t/\tTRUE\t0\tsession\tunrelated-session\n"
            ".ea.com\tTRUE\t/\tTRUE\t1\texpired\told\n",
            encoding="utf-8",
        )
    else:
        source = tmp_path / "cookies.json"
        records = [
            {
                "domain": "ea.com",
                "hostOnly": False,
                "name": "session",
                "value": "ea-session",
                "path": "/library",
                "secure": True,
                "httpOnly": True,
                "expirationDate": 4_000_000_000.5,
            },
            {"domain": ".ea.com.example", "name": "session", "value": "unrelated-session"},
            {"domain": ".ea.com", "name": "expired", "value": "old", "expirationDate": 1},
            {
                "domain": ".ea.com",
                "name": "partitioned",
                "value": "scoped",
                "partitionKey": {"topLevelSite": "https://other.example"},
            },
        ]
        source.write_text(json.dumps({"cookies": records} if format_name == "wrapped" else records), encoding="utf-8")
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(EAAppService, "cookies_path", str(target))
    monkeypatch.setattr(EAAppService, "get_access_token", lambda self, cookies: {"access_token": "fixture"})

    async def run() -> None:
        request = await store_login.begin("ea_app")
        await store_login.finish(request.id, str(source))
        assert (await services.get_source_status("ea_app")).signed_in

    asyncio.run(run())
    cookies = EAAppService().load_cookies()
    assert cookies is not None
    (cookie,) = cookies
    assert (cookie.domain, cookie.name, cookie.value) == (".ea.com", "session", "ea-session")
    assert cookie.secure and cookie.has_nonstandard_attr("HttpOnly")
    assert cookie.path == "/library" and cookie.expires == 4_000_000_000
    path = EAAppService().credentials.path
    (saved,) = json.loads(path.read_text(encoding="utf-8"))["cookies"]
    assert saved["name"] == "session" and saved["httpOnly"]
    assert path.stat().st_mode & 0o777 == 0o600
    assert not target.exists()


def test_cookie_import_keeps_saved_session_if_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / ".ea-cookies"
    legacy = ".ea.com\tTRUE\t/\tTRUE\t0\tsession\tprevious\n"
    target.write_text(legacy, encoding="utf-8")
    source = tmp_path / "cookies.json"

    def get_access_token(service: EAAppService, cookies) -> dict[str, str]:
        if not any(cookie.value == "accepted" for cookie in cookies):
            raise QtrisError("EA rejected the session")
        return {"access_token": "fixture"}

    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(EAAppService, "cookies_path", str(target))
    monkeypatch.setattr(EAAppService, "get_access_token", get_access_token)

    async def run() -> None:
        request = await store_login.begin("ea_app")
        source.write_text('{"cookies": [', encoding="utf-8")
        with pytest.raises(QtrisError, match="Select a JSON or Netscape cookie export"):
            await store_login.finish(request.id, str(source))
        source.write_text('[{"domain": "ea.com", "name": "session", "value": "rejected"}]', encoding="utf-8")
        with pytest.raises(QtrisError, match="EA rejected the session"):
            await store_login.finish(request.id, str(source))
        jar = EAAppService().load_cookies()
        assert jar is not None and [cookie.value for cookie in jar] == ["previous"]
        source.write_text('[{"domain": "ea.com", "name": "session", "value": "accepted"}]', encoding="utf-8")
        await store_login.finish(request.id, str(source))

    asyncio.run(run())
    assert not target.exists()
    (saved,) = EAAppService().credentials.read()["cookies"]
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
