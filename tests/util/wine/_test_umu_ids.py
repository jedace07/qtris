from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import requests

from qtris import settings
from qtris.util.wine import proton, umu_ids

if TYPE_CHECKING:
    from pathlib import Path


class FakeResponse:
    def __init__(self, data: object) -> None:
        self.data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self.data


@pytest.fixture
def requests_made(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, str]]:
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(umu_ids, "get_request_proxies", lambda _url: {})
    made: list[dict[str, str]] = []
    answers: dict[tuple[str, str], object] = {("gog", "1207658930"): [{"umu_id": "umu-1207658930"}]}

    def get(_url: str, *, params: dict[str, str], **_kwargs: Any) -> FakeResponse:
        made.append(params)
        if params["codename"] == "offline":
            raise requests.ConnectionError("offline")
        return FakeResponse(answers.get((params["store"], params["codename"]), []))

    monkeypatch.setattr(umu_ids.requests, "get", get)
    return made


def test_found_ids_are_reused_for_a_while(requests_made: list[dict[str, str]], monkeypatch: pytest.MonkeyPatch) -> None:
    assert proton.get_game_id("GOG", "1207658930") == "umu-1207658930"
    assert proton.get_game_id("gog", "1207658930") == "umu-1207658930"
    assert requests_made == [{"store": "gog", "codename": "1207658930"}]

    now = umu_ids.time.time()
    monkeypatch.setattr(umu_ids.time, "time", lambda: now + umu_ids.CACHE_SECONDS + 1)
    assert proton.get_game_id("gog", "1207658930") == "umu-1207658930"
    assert len(requests_made) == 2


def test_misses_and_failures_are_asked_again(requests_made: list[dict[str, str]]) -> None:
    assert proton.get_game_id("gog", "unknown") == proton.DEFAULT_GAMEID
    assert proton.get_game_id("gog", "unknown") == proton.DEFAULT_GAMEID
    assert proton.get_game_id("gog", "offline") == proton.DEFAULT_GAMEID
    assert [params["codename"] for params in requests_made] == ["unknown", "unknown", "offline"]


def test_store_names_follow_the_umu_database(requests_made: list[dict[str, str]]) -> None:
    assert umu_ids.get_umu_id("ea_app", "Origin.OFR.1") is None
    assert umu_ids.get_umu_id("itchio", "123") is None
    assert umu_ids.get_umu_id("", "123") is None
    assert requests_made == [{"store": "ea", "codename": "origin.ofr.1"}]
