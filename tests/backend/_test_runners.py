"""Built-in local platforms and downloadable Proton builds."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from qtris import runners as runner_registry
from qtris import settings
from qtris.backend import configuration, runners
from qtris.util.test_config import setup_test_environment
from qtris.util.wine import proton

setup_test_environment()


def test_local_platforms_are_available_even_with_old_disabled_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    read_bool_setting = settings.read_bool_setting

    def read_setting(key: str, default: bool = False, section: str = "qtris") -> bool:
        return False if section == "runners" else read_bool_setting(key, default, section)

    monkeypatch.setattr(settings, "read_bool_setting", read_setting)
    expected = [("linux", "Linux"), ("wine", "Windows"), ("web", "Web")]

    async def run() -> None:
        assert [(item.name, item.label) for item in await runners.list_local_game_platforms()] == expected
        assert await runners.list_runners() == ["linux", "web", "wine"]
        assert all(runner_registry.is_valid_runner_name(name) for name in ("steam", "flatpak"))

        form = await configuration.open_new_game_config()
        try:
            assert [(choice.value, choice.label) for choice in form.runner_choices] == expected
        finally:
            await configuration.close_config(form.id)

    asyncio.run(run())


def test_protonplus_builds_are_not_qtris_to_list_or_remove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proton_dir, compat_dir = tmp_path / "proton", tmp_path / "compatibilitytools.d"
    for folder in (compat_dir / "GE-Proton11-12", compat_dir / "GE-Proton11-11"):
        folder.mkdir(parents=True)
        (folder / "proton").touch()
    (compat_dir / "GE-Proton11-12" / proton.PROTONPLUS_MARKER).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(proton.LINUX_SYSTEM, "arch", "x86_64")
    monkeypatch.setattr(proton, "get_proton_dir", lambda: str(proton_dir))
    monkeypatch.setattr(proton, "get_steam_compat_dir", lambda: str(compat_dir))
    monkeypatch.setattr(proton, "get_proton_versions", dict)

    assert runners._managed_proton_path("GE-Proton11-12") is None
    assert runners._managed_proton_path("GE-Proton11-11") == compat_dir / "GE-Proton11-11"
    builds = asyncio.run(runners.list_proton_builds("GE-Proton"))
    assert [(build.version, build.installed, build.can_remove) for build in builds] == [("GE-Proton11-11", True, True)]
