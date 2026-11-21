"""Adopt the profile of the Lutris fork Qtris grew out of.

On its first start Qtris moves an existing Lutris profile (configuration, data, cache
and game icons) to its own folders, renames lutris.conf, and rewrites the paths the
profile stored. It looks for the profile of Lutris run from source and, when Qtris is
a Flatpak, the one the Lutris Flatpak kept in its own folder.

This runs before anything else creates Qtris's folders, so it imports only the
standard library and GLib.
"""

from __future__ import annotations

import configparser
import os
import shutil
import sqlite3
import sys
from pathlib import Path

from gi.repository import GLib

LEGACY_NAME = "lutris"
NAME = "qtris"
LEGACY_FLATPAK_ID = "net.lutris.Lutris"

_TEST_MODULES = ("unittest", "pytest")


def _legacy_bases(kind: str) -> list[Path]:
    """Return the folders that held Lutris's folders of this kind (data, config or cache)."""
    home = Path.home()
    native = {"data": home / ".local" / "share", "config": home / ".config", "cache": home / ".cache"}[kind]
    bases = [native]
    if os.path.exists("/.flatpak-info"):
        bases.insert(0, home / ".var" / "app" / LEGACY_FLATPAK_ID / kind)
    return bases


def _current_base(kind: str) -> Path:
    getter = {"data": GLib.get_user_data_dir, "config": GLib.get_user_config_dir, "cache": GLib.get_user_cache_dir}
    return Path(getter[kind]())


def _move(source: Path, target: Path) -> bool:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(source, target)
    except OSError as ex:
        sys.stderr.write(f"Could not move {source} to {target}: {ex}\n")
        return False
    return True


def _rewrite_text(path: Path, replacements: list[tuple[str, str]]) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return
    new_text = text
    for old, new in replacements:
        new_text = new_text.replace(old, new)
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")


def _rewrite_database(path: Path, replacements: list[tuple[str, str]]) -> None:
    """Replace the old folders in every text column of the library database."""
    with sqlite3.connect(path) as connection:
        tables = [row[0] for row in connection.execute("select name from sqlite_master where type = 'table'")]
        for table in tables:
            columns = [row[1] for row in connection.execute(f'pragma table_info("{table}")') if row[2] == "TEXT"]
            for column in columns:
                for old, new in replacements:
                    connection.execute(
                        f'update "{table}" set "{column}" = replace("{column}", ?, ?) where instr("{column}", ?) > 0',
                        (old, new, old),
                    )


class _CaseKeepingParser(configparser.RawConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def _adopt_config_file(folder: Path) -> None:
    """Rename lutris.conf to qtris.conf, and its [lutris] section to [qtris]."""
    legacy = folder / f"{LEGACY_NAME}.conf"
    target = folder / f"{NAME}.conf"
    if not legacy.is_file() or target.exists():
        return
    parser = _CaseKeepingParser()
    parser.read(legacy, encoding="utf-8")
    if parser.has_section(LEGACY_NAME) and not parser.has_section(NAME):
        parser.add_section(NAME)
        for key, value in parser.items(LEGACY_NAME):
            parser.set(NAME, key, value)
        parser.remove_section(LEGACY_NAME)
    with target.open("w", encoding="utf-8") as config_file:
        parser.write(config_file)
    legacy.unlink()


def _adopt_icons(legacy_data: Path, data: Path) -> None:
    """Move the game icons Lutris installed (lutris_<slug>.png) to Qtris's names."""
    relative = Path("icons") / "hicolor" / "128x128" / "apps"
    target_dir = data / relative
    for icon in (legacy_data / relative).glob(f"{LEGACY_NAME}_*.png"):
        target = target_dir / f"{NAME}_{icon.name[len(LEGACY_NAME) + 1 :]}"
        if not target.exists():
            _move(icon, target)


def adopt_lutris_profile() -> bool:
    """Move a Lutris profile to Qtris's folders, if Qtris has none yet. Return True if one moved."""
    if any(module in sys.modules for module in _TEST_MODULES):
        return False
    replacements: list[tuple[str, str]] = []
    moved: dict[str, Path] = {}
    for kind in ("config", "data", "cache"):
        target = _current_base(kind) / NAME
        if target.exists():
            continue
        for base in _legacy_bases(kind):
            source = base / LEGACY_NAME
            if source.is_dir() and not source.is_symlink() and _move(source, target):
                replacements.append((str(source), str(target)))
                moved[kind] = target
                if kind == "data":
                    _adopt_icons(base, _current_base("data"))
                break
    if not moved:
        return False

    # Paths may be stored with '~' for the home folder, too
    home = str(Path.home())
    replacements += [(old.replace(home, "~", 1), new.replace(home, "~", 1)) for old, new in replacements]
    for kind in ("config", "data"):
        if folder := moved.get(kind):
            _adopt_config_file(folder)
            configs = [folder / f"{NAME}.conf", folder / "system.yml"]
            configs += [*folder.glob("games/*.yml"), *folder.glob("runners/*.yml")]
            for path in configs:
                if path.is_file():
                    _rewrite_text(path, replacements)
    if (database := moved.get("data", Path()) / "pga.db").is_file():
        try:
            _rewrite_database(database, replacements)
        except sqlite3.Error as ex:
            sys.stderr.write(f"Could not update the folders in {database}: {ex}\n")
    sys.stderr.write("Moved the Lutris profile to Qtris's folders: %s\n" % ", ".join(map(str, moved.values())))
    return True
