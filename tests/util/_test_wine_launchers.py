from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from qtris.exceptions import QtrisError
from qtris.util.wine.launchers import LAUNCHER_SETUPS, repair_ea_launcher, reserve_launcher_prefix

if TYPE_CHECKING:
    from pathlib import Path


def test_launcher_setup_reuses_only_its_own_prefix(tmp_path: Path) -> None:
    target = tmp_path / "prefix"
    target.mkdir()
    (target / "existing.txt").touch()
    with pytest.raises(QtrisError, match="empty folder"):
        reserve_launcher_prefix(str(target), "battlenet")

    (target / "existing.txt").unlink()
    reserve_launcher_prefix(str(target), "battlenet")
    (target / "user.reg").touch()
    reserve_launcher_prefix(str(target), "battlenet")
    with pytest.raises(QtrisError, match="empty folder"):
        reserve_launcher_prefix(str(target), "ea_app")


def test_ea_repair_uses_the_latest_complete_update_and_skips_work_on_relaunch(tmp_path: Path) -> None:
    exe = tmp_path / LAUNCHER_SETUPS["ea_app"].executable
    base = exe.parent.parent
    for version in ("13.9.1", "13.10.1"):
        staged = base / version / "EA Desktop" / exe.name
        staged.parent.mkdir(parents=True)
        staged.write_text(version, encoding="utf-8")
    (base / "14.1.0").mkdir()

    repair_ea_launcher(tmp_path, exe)
    assert exe.read_text(encoding="utf-8") == "13.10.1"
    exe.write_text("current", encoding="utf-8")
    repair_ea_launcher(tmp_path, exe)
    assert exe.read_text(encoding="utf-8") == "current"

    newer = base / "14.1.0" / "EA Desktop" / exe.name
    newer.parent.mkdir()
    newer.write_text("14.1.0", encoding="utf-8")
    repair_ea_launcher(tmp_path, exe)
    assert exe.read_text(encoding="utf-8") == "14.1.0"
    assert newer.is_file()
