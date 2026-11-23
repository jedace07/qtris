"""Persist Qtris-managed credentials in a private JSON file per store."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import TYPE_CHECKING, Literal

from qtris.util.cookies import cookie_records, load_cookie_file
from qtris.util.log import logger

if TYPE_CHECKING:
    from typing import Any


_lock = RLock()


def protect_config_directory(directory: str | Path) -> None:
    path = Path(directory)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o777 != 0o700:
        path.chmod(0o700)


def save_credentials(filename: str | Path, text: str) -> None:
    path = Path(filename)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete_on_close=False) as output:
        output.write(text)
        output.flush()
        os.fsync(output.fileno())
        Path(output.name).replace(path)


@dataclass(frozen=True)
class LegacyCredential:
    field: str
    filename: str
    format: Literal["cookies", "json", "text"]
    timestamp_field: str = ""


@dataclass(frozen=True)
class StoreCredentials:
    path: Path
    legacy: tuple[LegacyCredential, ...]

    def _remove(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Unable to remove store credentials at %s", path)

    def _write(self, data: dict[str, Any]) -> None:
        protect_config_directory(self.path.parent)
        save_credentials(self.path, json.dumps(data, indent=2))

    def _read(self) -> tuple[dict[str, Any], list[Path]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except OSError, ValueError:
            logger.warning("Unable to read store credentials at %s", self.path)
            return {}, []
        else:
            if isinstance(data, dict):
                return data, []
            logger.warning("Invalid store credentials at %s", self.path)
            return {}, []

        data: dict[str, Any] = {}
        migrated: list[Path] = []
        for legacy in self.legacy:
            path = Path(legacy.filename)
            try:
                if legacy.format == "cookies":
                    value = cookie_records(load_cookie_file(path))
                else:
                    text = path.read_text(encoding="utf-8")
                    value = json.loads(text) if legacy.format == "json" else text.strip()
                if legacy.timestamp_field:
                    data[legacy.timestamp_field] = path.stat().st_mtime
            except FileNotFoundError:
                continue
            except OSError, ValueError:
                logger.warning("Unable to migrate store credentials at %s", path)
                continue
            data[legacy.field] = value
            migrated.append(path)
        return data, migrated

    def read(self) -> dict[str, Any]:
        with _lock:
            data, migrated = self._read()
            if migrated:
                try:
                    self._write(data)
                except OSError:
                    logger.warning("Unable to save migrated store credentials at %s", self.path)
                else:
                    for path in migrated:
                        self._remove(path)
            return data

    def update(self, *, expected: dict[str, Any] | None = None, **fields: Any) -> bool:
        with _lock:
            data, _ = self._read()
            if expected is not None and any(data.get(key) != value for key, value in expected.items()):
                return False
            self._write(data | fields)
            for legacy in self.legacy:
                self._remove(Path(legacy.filename))
            return True

    def clear(self) -> None:
        with _lock:
            for legacy in self.legacy:
                Path(legacy.filename).unlink(missing_ok=True)
            self.path.unlink(missing_ok=True)
