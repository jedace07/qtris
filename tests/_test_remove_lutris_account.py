"""Forget a Lutris.net account a previous version connected."""

from pathlib import Path

import pytest

from qtris import settings
from qtris.migrations import remove_lutris_account
from qtris.util.settings import SettingsIO


def test_account_files_and_sync_settings_are_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "auth-token").write_text("someone:secret", encoding="utf-8")
    (cache / "user.json").write_text("{}", encoding="utf-8")
    (cache / "keep.json").write_text("{}", encoding="utf-8")
    config_file = tmp_path / "qtris.conf"
    config_file.write_text(
        "[qtris]\nlibrary_sync_enabled = True\nlast_library_sync_at = 1\nwidth = 800\n", encoding="utf-8"
    )
    monkeypatch.setattr(settings, "CACHE_DIR", str(cache))
    monkeypatch.setattr(settings, "sio", SettingsIO(str(config_file)))

    remove_lutris_account.migrate()
    remove_lutris_account.migrate()

    assert sorted(path.name for path in cache.iterdir()) == ["keep.json"]
    saved = SettingsIO(str(config_file))
    assert saved.read_setting("library_sync_enabled") == "" and saved.read_setting("last_library_sync_at") == ""
    assert saved.read_setting("width") == "800"
