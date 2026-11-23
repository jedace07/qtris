from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from qtris import settings
from qtris.services.itchio import ItchIoGame, ItchIoService

if TYPE_CHECKING:
    from pathlib import Path


def test_itchio_oauth_library_reads_past_short_pages_and_deduplicates_by_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service = ItchIoService()
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(service, "cache_path", str(tmp_path))
    monkeypatch.setattr(service, "key_cache_file", str(tmp_path / "profile" / "owned.json"))
    monkeypatch.setattr(service, "games_cache_path", str(tmp_path / "games"))
    service.save_oauth_token("fixture")
    queries: list[dict[str, int]] = []
    saved: list[ItchIoGame] = []

    def fetch_owned_keys(query: dict[str, int]) -> dict[str, Any]:
        queries.append(query)
        keys = {
            1: [{"id": 10, "game": {"id": 1, "title": "Same title", "traits": ["p_linux"]}}],
            2: [
                {"id": 20, "game": {"id": 2, "title": "Same title", "traits": ["p_linux"]}},
                {"id": 30, "game": {"id": 1, "title": "Same title", "traits": ["p_linux"]}},
            ],
            3: [],
        }
        return {"per_page": 50, "owned_keys": keys[query["page"]]}

    def unexpected_collections() -> list[dict[str, Any]]:
        raise AssertionError("An OAuth token should not be used to fetch collections")

    monkeypatch.setattr(service, "fetch_owned_keys", fetch_owned_keys)
    monkeypatch.setattr(service, "get_collection_list", unexpected_collections)
    monkeypatch.setattr(ItchIoGame, "save", lambda game: saved.append(game))

    assert service.load() == saved
    assert [game.appid for game in saved] == ["1", "2"]
    assert [query["page"] for query in queries] == [1, 2, 3]
    assert service.get_key("1") == 10
