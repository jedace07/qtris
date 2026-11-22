"""Tests for the Gamescope options Qtris launches games with."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from qtris import settings
from qtris.config import QtrisConfig
from qtris.runner_interpreter import get_gamescope_args

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the user's own system and runner settings out of the defaults."""
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "RUNNERS_CONFIG_DIR", str(tmp_path / "runners"))


def test_gamescope_uses_its_own_resolution_defaults() -> None:
    config = QtrisConfig(runner_slug="wine")

    args = get_gamescope_args(["game.exe"], config.system_config)

    assert {"-W", "-H", "-w", "-h"}.isdisjoint(args)


def test_gamescope_uses_a_configured_output_resolution() -> None:
    config = QtrisConfig(runner_slug="wine")
    config.system_level["system"] = {"gamescope_output_res": "1280x720"}
    config.update_cascaded_config()

    args = get_gamescope_args(["game.exe"], config.system_config)

    assert args[1:5] == ["-W", "1280", "-H", "720"]


def test_fullscreen_fsr_is_off_by_default() -> None:
    assert QtrisConfig(runner_slug="wine").runner_config["fsr"] is False
