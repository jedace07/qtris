"""Other apps' folders, reached from a sandbox that can't see them.

The sandbox's home and the host's are separate folders here, and a stand-in for
flatpak-spawn runs the real scripts on the host's. Direct access from the sandbox finds
nothing, and what it writes never reaches the host, as in the Flatpak.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING
from unittest.mock import MagicMock
from zipfile import ZipFile

import pytest

from qtris import settings
from qtris.delegates import InstallUIDelegate
from qtris.runners.eden import eden
from qtris.runners.libretro import libretro
from qtris.runners.ryubing import ryubing
from qtris.util import flatpak, host_files
from qtris.util.retroarch import content

if TYPE_CHECKING:
    from pathlib import Path

RETROARCH = ".var/app/org.libretro.RetroArch/config/retroarch"

_FAKE_SPAWN = """#!{python}
import subprocess, sys
sandbox, host = {sandbox!r}, {host!r}
assert sys.argv[1:4] == ["--host", "--watch-bus", "--directory=/"], sys.argv
command = [argument.replace(sandbox, host) for argument in sys.argv[4:]]
result = subprocess.run(command, stdout=subprocess.PIPE, cwd="/")
sys.stdout.buffer.write(result.stdout.replace(host.encode(), sandbox.encode()))
sys.exit(result.returncode)
"""


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    sandbox, host = tmp_path / "sandbox-home", tmp_path / "host-home"
    sandbox.mkdir()
    host.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    spawn = tools / "flatpak-spawn"
    spawn.write_text(_FAKE_SPAWN.format(python=sys.executable, sandbox=str(sandbox), host=str(host)), encoding="utf-8")
    spawn.chmod(0o755)
    monkeypatch.setenv("HOME", str(sandbox))
    monkeypatch.setenv("FLATPAK_ID", settings.APPLICATION_ID)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path / "cache"))
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda path: path == "/.flatpak-info" or real_exists(path))
    return sandbox, host


def put(path: Path, data: bytes = b"data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_only_other_apps_folders_go_through_the_host(homes: tuple[Path, Path]) -> None:
    sandbox, _host = homes
    assert host_files._get_host_path(sandbox / RETROARCH) == str(sandbox / RETROARCH)
    assert host_files._get_host_path("~/.var/app/dev.eden_emu.eden")
    assert not host_files._get_host_path(sandbox / ".var/app" / settings.APPLICATION_ID / "data")
    assert not host_files._get_host_path(sandbox / "Games/game.rom")
    assert not host_files._get_host_path(sandbox / ".var/app")


def test_outside_the_flatpak_files_are_used_directly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", "")
    target = tmp_path / RETROARCH / "cores" / "core.so"
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, "exists", lambda path: path != "/.flatpak-info" and real_exists(path))
    host_files.write_bytes(target, b"core")
    assert target.read_bytes() == b"core" == host_files.read_bytes(target)


@pytest.mark.parametrize("name", ["plain", "two words", "it's", '"$(touch owned)"', "-dash", "[1] *", "new\nline"])
def test_any_file_name_is_safe(homes: tuple[Path, Path], name: str) -> None:
    sandbox, host = homes
    path = sandbox / RETROARCH / name / f"{name}.bin"
    assert not host_files.exists(path)
    assert host_files.read_bytes(path) is None
    host_files.write_bytes(path, b"\0\xff binary\n\n")
    assert (host / RETROARCH / name / f"{name}.bin").read_bytes() == b"\0\xff binary\n\n"
    assert host_files.read_bytes(path) == b"\0\xff binary\n\n"
    assert host_files.exists(path)
    assert not (sandbox / ".var").exists()


def test_writing_keeps_a_files_mode_and_follows_its_link(homes: tuple[Path, Path]) -> None:
    sandbox, host = homes
    real = put(host / "shared" / "prod.keys", b"old")
    real.chmod(0o600)
    link = host / RETROARCH / "prod.keys"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    host_files.write_bytes(sandbox / RETROARCH / "prod.keys", b"new")
    assert link.is_symlink() and real.read_bytes() == b"new" and real.stat().st_mode & 0o777 == 0o600


def test_an_incomplete_copy_never_replaces_a_file(homes: tuple[Path, Path]) -> None:
    sandbox, host = homes
    target = put(host / RETROARCH / "core.so", b"whole")
    with pytest.raises(host_files.HostFileError):
        # Fewer bytes arrive than were promised, as when Qtris dies mid-copy
        host_files._run(host_files._WRITE, str(sandbox / RETROARCH / "core.so"), "99", data=b"part")
    assert target.read_bytes() == b"whole"
    assert [path.name for path in target.parent.iterdir()] == ["core.so"]


def test_an_unreachable_host_is_an_error_not_a_missing_file(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, _host = homes
    broken = put(tmp_path / "broken" / "flatpak-spawn", b"#!/bin/sh\necho 'Portal call failed' >&2\nexit 1\n")
    broken.chmod(0o755)
    monkeypatch.setenv("PATH", f"{broken.parent}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(host_files.HostFileError, match="Portal call failed"):
        host_files.exists(sandbox / RETROARCH)


def test_eden_gets_its_keys(homes: tuple[Path, Path]) -> None:
    sandbox, host = homes
    (host / ".var/app/dev.eden_emu.eden/data/eden/nand").mkdir(parents=True)
    keys = put(sandbox / "keys" / "prod.keys", b"keys")
    runner = eden()
    runner._config = MagicMock(runner_config={"prod_keys": str(keys)})
    runner.prelaunch()
    assert (host / ".var/app/dev.eden_emu.eden/data/eden/keys/prod.keys").read_bytes() == b"keys"
    assert not (sandbox / ".var").exists()


def test_ryubing_gets_its_keys_even_from_another_emulators_folder(homes: tuple[Path, Path]) -> None:
    sandbox, host = homes
    put(host / ".var/app/dev.eden_emu.eden/data/eden/keys/title.keys", b"title")
    runner = ryubing()
    runner._config = MagicMock(
        runner_config={"title_keys": str(sandbox / ".var/app/dev.eden_emu.eden/data/eden/keys/title.keys")}
    )
    runner.prelaunch()
    target = host / ".var/app/io.github.ryubing.Ryujinx/config/Ryujinx/system/title.keys"
    assert target.read_bytes() == b"title"
    assert not (sandbox / ".var").exists()


@pytest.mark.parametrize("cancelled", [False, True])
def test_retroarch_cores_install_where_its_config_says(
    homes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    sandbox, host = homes
    put(host / RETROARCH / "retroarch.cfg", f'libretro_directory = "~/{RETROARCH}/my cores"\n'.encode())
    monkeypatch.setattr(flatpak, "is_app_installed", lambda appid: True)

    def download(url: str, destination: str) -> bool:
        if cancelled:
            return False
        with ZipFile(destination, "w") as archive:
            archive.writestr("mgba_libretro.so", b"core")
        return True

    delegate = InstallUIDelegate()
    delegate.download_install_file = download
    runner = libretro()
    callback = MagicMock()
    runner.install(delegate, version="mgba", callback=callback)

    core = host / RETROARCH / "my cores" / "mgba_libretro.so"
    assert core.exists() is not cancelled
    assert runner.is_installed(core="mgba") is not cancelled
    assert callback.called is not cancelled
    assert not (sandbox / ".var").exists()


def test_roms_in_retroarchs_folder_still_play(homes: tuple[Path, Path]) -> None:
    sandbox, host = homes
    put(host / RETROARCH / "downloads/game.gba")
    rom = str(sandbox / RETROARCH / "downloads/game.gba")
    assert content.get_content_path("mgba", {"main_file": rom}) == rom
