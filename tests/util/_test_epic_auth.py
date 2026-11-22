"""Run Epic's browser sign-in against a stand-in for its APIs."""

import asyncio
import logging
import threading
from typing import Any

import pytest
import requests

from qtris.util import epic_auth


class Response:
    def __init__(self, status: int, body: object = None) -> None:
        self.status_code = status
        self.body = body

    def json(self) -> object:
        if self.body is None:
            raise ValueError("No JSON")
        return self.body


def serve(monkeypatch: pytest.MonkeyPatch, *answers: Response | Exception) -> list[tuple[str, str, dict[str, Any]]]:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    remaining = list(answers)

    def request(method: str, url: str, **options: Any) -> Response:
        calls.append((method, url, options))
        answer = remaining.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(requests, "request", request)
    monkeypatch.setattr(epic_auth, "get_request_proxies", lambda url: {})
    return calls


def failure(code: str, status: int = 400) -> Response:
    return Response(status, {"errorCode": f"errors.com.epicgames.{code}"})


def login(expires_in: float = 5) -> epic_auth.DeviceLogin:
    return epic_auth.DeviceLogin("https://www.epicgames.com/activate", "CODE", "SECRET-DEVICE", 0, expires_in)


SESSION = epic_auth.ApprovedSession("SECRET-REFRESH")


def test_starts_with_the_launchers_client_and_polls_slower_than_epics_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "verification_uri_complete": "https://www.epicgames.com/activate?userCode=CODE",
        "user_code": "CODE",
        "device_code": "SECRET-DEVICE",
        "interval": 10,
        "expires_in": 600,
    }
    calls = serve(monkeypatch, Response(200, body))
    started = epic_auth.start()
    assert (started.user_code, started.interval, started.expires_in) == ("CODE", 11, 600)
    assert calls[0][1] == "https://api.epicgames.dev/epic/oauth/v1/deviceAuthorization"
    assert calls[0][2]["auth"][0] == "34a02cf8f4414e29b15921876da36f9a"
    assert "SECRET" not in repr(started) + repr(SESSION)


@pytest.mark.parametrize(
    "url",
    [
        "http://www.epicgames.com/activate",
        "https://www.epicgames.com.example/activate",
        "https://user@www.epicgames.com/activate",
        "file:///etc/passwd",
    ],
)
def test_only_opens_epics_own_site(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    serve(monkeypatch, Response(200, {"verification_uri_complete": url, "user_code": "C", "device_code": "D"}))
    with pytest.raises(epic_auth.EpicAuthError, match="could not start"):
        epic_auth.start()


def test_waits_through_pending_answers_and_dropped_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = serve(
        monkeypatch,
        failure("account.oauth.authorization_pending"),
        requests.ConnectionError("offline"),
        Response(503),
        Response(200, {"refresh_token": "SECRET-REFRESH"}),
    )
    assert asyncio.run(epic_auth.wait_for_approval(login())) == SESSION
    assert len(calls) == 4


def test_slows_down_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, failure("common.slow_down"), Response(200, {"refresh_token": "SECRET-REFRESH"}))
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    assert asyncio.run(epic_auth.wait_for_approval(login())) == SESSION
    assert waits == [0, 5]


def test_stops_when_refused_or_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, failure("not_found", 404))
    with pytest.raises(epic_auth.EpicAuthError, match="declined"):
        asyncio.run(epic_auth.wait_for_approval(login()))
    with pytest.raises(epic_auth.EpicAuthError, match="expired"):
        asyncio.run(epic_auth.wait_for_approval(login(expires_in=0)))


def test_revokes_an_approval_that_arrives_while_cancelling(monkeypatch: pytest.MonkeyPatch) -> None:
    polling, release = threading.Event(), threading.Event()
    revoked: list[epic_auth.ApprovedSession] = []

    def poll(device: epic_auth.DeviceLogin) -> epic_auth.ApprovedSession:
        polling.set()
        release.wait(5)
        return SESSION

    monkeypatch.setattr(epic_auth, "_poll", poll)
    monkeypatch.setattr(epic_auth, "revoke", revoked.append)

    async def run() -> None:
        waiting = asyncio.create_task(epic_auth.wait_for_approval(login()))
        assert await asyncio.to_thread(polling.wait, 5)
        waiting.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiting

    asyncio.run(run())
    assert revoked == [SESSION]


def test_converts_the_session_and_does_not_revoke_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls = serve(monkeypatch, Response(200, {"access_token": "SECRET-ACCESS"}), Response(200, {"code": "exchange"}))
    accepted: list[str] = []
    with caplog.at_level(logging.DEBUG):
        epic_auth.hand_over(SESSION, accepted.append)
    assert accepted == ["exchange"]
    assert [(method, url.rsplit("/", 1)[1]) for method, url, _options in calls] == [
        ("POST", "token"),
        ("GET", "exchange"),
    ]
    assert calls[0][2]["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": "SECRET-REFRESH",
        "token_type": "eg1",
    }
    assert calls[1][2]["headers"] == {"Authorization": "bearer SECRET-ACCESS"}
    assert "SECRET" not in caplog.text


@pytest.mark.parametrize("failing", ["refresh", "agreement", "exchange", "offline", "legendary"])
def test_revokes_what_it_made_when_the_hand_over_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failing: str
) -> None:
    converted = Response(200, {"access_token": "SECRET-ACCESS"})
    done = Response(204)
    answers: dict[str, list[Response | Exception]] = {
        "refresh": [failure("account.auth_token.invalid_refresh_token"), done],
        "agreement": [failure("oauth.corrective_action_required"), done],
        "exchange": [converted, failure("common.missing_permission", 403), done],
        "offline": [converted, requests.ConnectionError("no route to SECRET-ACCESS"), done],
        "legendary": [converted, Response(200, {"code": "exchange"}), done],
    }
    calls = serve(monkeypatch, *answers[failing])

    def reject(code: str) -> None:
        raise epic_auth.EpicAuthError("legendary refused")

    with caplog.at_level(logging.DEBUG), pytest.raises(epic_auth.EpicAuthError) as raised:
        epic_auth.hand_over(SESSION, reject)
    method, url, _options = calls[-1]
    if failing in ("refresh", "agreement"):
        assert method == "POST" and url.endswith("/v2/revoke")
    else:
        assert method == "DELETE" and url.endswith("/sessions/kill/SECRET-ACCESS")
        # Never the kill types that would sign out the user's other sessions
        assert "?" not in url
    assert "SECRET" not in caplog.text + raised.value.message
