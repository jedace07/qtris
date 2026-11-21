"""Tests for the Gamescope options Qtris launches games with."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from qtris import settings, sysoptions
from qtris.config import QtrisConfig
from qtris.runner_interpreter import get_gamescope_args


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the user's own system and runner settings out of the defaults."""
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "RUNNERS_CONFIG_DIR", str(tmp_path / "runners"))


def _monitor(width: str, height: str) -> MagicMock:
    display_manager = MagicMock()
    display_manager.get_current_resolution.return_value = (width, height)
    return display_manager


def test_gamescope_outputs_at_the_monitor_resolution_by_default() -> None:
    with patch.object(sysoptions, "get_display_manager", return_value=_monitor("2560", "1440")):
        system_config = dict(QtrisConfig(runner_slug="wine").system_config, gamescope=True)

    assert system_config["gamescope_output_res"] == "2560x1440"
    # The game renders at the output resolution unless one is chosen for it
    assert not system_config.get("gamescope_game_res")
    args = get_gamescope_args(["game.exe"], system_config)
    assert args[args.index("-W") + 1 : args.index("-W") + 4] == ["2560", "-H", "1440"]
    assert "-w" not in args


def test_a_chosen_output_resolution_wins_over_the_monitor() -> None:
    with patch.object(sysoptions, "get_display_manager", return_value=_monitor("2560", "1440")):
        config = QtrisConfig(runner_slug="wine")
        config.system_level["system"] = {"gamescope_output_res": "1280x720"}
        config.update_cascaded_config()

    assert config.system_config["gamescope_output_res"] == "1280x720"


def test_fullscreen_fsr_is_off_by_default() -> None:
    assert QtrisConfig(runner_slug="wine").runner_config["fsr"] is False
