from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from qtris import settings
from qtris.services.gog import GOGService
from qtris.services.zoom import ZoomService
from qtris.util import credentials


def test_gog_migrates_its_token_and_cookies_without_touching_zoom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    gog = GOGService()
    zoom = ZoomService()
    legacy_token = tmp_path / ".gog.token"
    legacy_cookies = tmp_path / ".gog.auth"
    zoom_token = tmp_path / ".zoom.token"
    monkeypatch.setattr(gog, "token_path", str(legacy_token))
    monkeypatch.setattr(gog, "cookies_path", str(legacy_cookies))
    monkeypatch.setattr(gog, "cache_path", "")
    monkeypatch.setattr(gog, "wipe_game_cache", lambda: None)
    monkeypatch.setattr(zoom, "token_path", str(zoom_token))
    token = {"access_token": "gog-access", "refresh_token": "gog-refresh"}
    legacy_token.write_text(json.dumps(token), encoding="utf-8")
    os.utime(legacy_token, (30, 30))
    legacy_cookies.write_text(".gog.com\tTRUE\t/\tTRUE\t0\tsession\tfixture\n", encoding="utf-8")
    zoom_token.write_text("zoom-token", encoding="utf-8")

    assert zoom.load_token() == "zoom-token"
    assert gog.load_token() == token
    assert gog.credentials.read()["token_updated_at"] == 30
    assert not legacy_token.exists() and not legacy_cookies.exists() and not zoom_token.exists()
    assert gog.credentials.path.stat().st_mode & 0o777 == 0o600
    assert gog.credentials.path.parent.stat().st_mode & 0o777 == 0o700
    assert zoom.credentials.read() == {"token": "zoom-token"}
    gog.logout()
    assert not gog.store_refreshed_token({"access_token": "late-refresh"}, token)
    assert not gog.credentials.path.exists()
    assert zoom.load_token() == "zoom-token"


def test_failed_migration_keeps_the_working_legacy_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    original = tmp_path / "legacy"
    original.write_text("original-key", encoding="utf-8")
    store = credentials.StoreCredentials(
        tmp_path / "store.json", (credentials.LegacyCredential("key", str(original), "text"),)
    )

    def fail(filename: str | Path, text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(credentials, "save_credentials", fail)
    assert store.read() == {"key": "original-key"}
    assert original.read_text(encoding="utf-8") == "original-key"
    with pytest.raises(OSError, match="disk full"):
        store.update(key="new-key")
    assert original.exists()


def test_gogdl_uses_a_temporary_auth_file_and_saves_refresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from qtris.util import gogdl

    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(gogdl, "_get_gogdl_binary", lambda: "/fake/gogdl")
    service = GOGService()
    service.credentials.update(token={"access_token": "initial", "refresh_token": "initial-refresh"})
    monkeypatch.setattr(GOGService, "get_token_age", lambda self: 0.0)
    auth_paths: list[Path] = []

    def run(cmd: list[str], command: str, progress_callback: object) -> dict[str, str]:
        auth_path = Path(cmd[cmd.index("--auth-config-path") + 1])
        auth_paths.append(auth_path)
        assert auth_path.stat().st_mode & 0o777 == 0o600
        data = json.loads(auth_path.read_text(encoding="utf-8"))
        assert data[gogdl.GOG_CLIENT_ID]["access_token"] == "initial"
        data[gogdl.GOG_CLIENT_ID]["access_token"] = "refreshed"
        auth_path.write_text(json.dumps(data), encoding="utf-8")
        return {"result": "ok"}

    monkeypatch.setattr(gogdl, "_run_command", run)
    assert gogdl.run_gogdl("info", "123", "/fake/game") == {"result": "ok"}
    assert not auth_paths[0].exists()
    assert service.load_token()["access_token"] == "refreshed"
