"""Read what Steam's local app cache, appcache/appinfo.vdf, says about apps.

Steam keeps the store's metadata for every app it has seen in this binary file,
including the operating systems each app has builds for. Its entries are read
only for the apps asked about, since the file holds thousands of apps."""

from __future__ import annotations

import os
import struct
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from qtris.util.log import logger
from qtris.util.steam import vdf
from qtris.util.steam.config import get_steam_dir

if TYPE_CHECKING:
    from collections.abc import Sequence

# The magic numbers of the versions this reads: version 29 stores keys in a string
# table at the end of the file, version 28 added a hash of each entry's binary VDF
_APPINFO_V27 = 0x07564427
_APPINFO_V28 = 0x07564428
_APPINFO_V29 = 0x07564429
_APPID = struct.Struct("<I")
_SIZE = struct.Struct("<I")
# What comes between an entry's size and its binary VDF: info state, last update,
# access token, SHA-1 of the text VDF and change number; and from version 28, the
# SHA-1 of the binary VDF
_ENTRY_METADATA_SIZE = {_APPINFO_V27: 40, _APPINFO_V28: 60, _APPINFO_V29: 60}


@dataclass(frozen=True)
class _AppInfoIndex:
    """Where each app's binary VDF lies in one version of appinfo.vdf."""

    path: str
    mtime_ns: int
    key_table: Sequence[str] | None
    # Byte ranges by app ID
    entries: dict[int, tuple[int, int]]


_lock = threading.Lock()
_index: _AppInfoIndex | None = None
_oslists: dict[int, tuple[str, ...]] = {}


def get_app_oslist(appid: str | int) -> tuple[str, ...]:
    """Return the operating systems Steam has builds of an app for, such as
    ("windows", "linux"), or an empty tuple if Steam's cache doesn't say."""
    try:
        appid = int(appid)
    except TypeError, ValueError:
        return ()
    with _lock:
        index = _get_index()
        if not index or appid not in index.entries:
            return ()
        if appid not in _oslists:
            _oslists[appid] = _read_oslist(index, appid)
        return _oslists[appid]


def _get_index() -> _AppInfoIndex | None:
    """Return the index of Steam's appinfo.vdf, indexing it again once Steam rewrites it."""
    global _index
    steam_dir = get_steam_dir()
    path = os.path.join(steam_dir, "appcache/appinfo.vdf") if steam_dir else ""
    if not os.path.isfile(path):
        return None
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except OSError:
        return None
    if _index and _index.path == path and _index.mtime_ns == mtime_ns:
        return _index
    _oslists.clear()
    try:
        _index = _read_index(path, mtime_ns)
    except (OSError, ValueError, struct.error) as ex:
        logger.warning("Unable to read Steam's app cache %s: %s", path, ex)
        # Not read again until Steam rewrites it
        _index = _AppInfoIndex(path, mtime_ns, None, {})
    return _index


def _read_index(path: str, mtime_ns: int) -> _AppInfoIndex:
    with open(path, "rb") as appinfo_file:
        magic = struct.unpack("<II", appinfo_file.read(8))[0]
        if magic not in _ENTRY_METADATA_SIZE:
            raise ValueError("unknown version %#x" % magic)
        key_table = None
        if magic == _APPINFO_V29:
            (key_table_offset,) = struct.unpack("<q", appinfo_file.read(8))
            data_start = appinfo_file.tell()
            appinfo_file.seek(key_table_offset)
            (key_count,) = struct.unpack("<I", appinfo_file.read(4))
            keys = appinfo_file.read().split(b"\x00")[:key_count]
            key_table = tuple(key.decode("utf-8", "replace") for key in keys)
            appinfo_file.seek(data_start)

        entries = {}
        # The entries end with app ID 0
        while (appid := _APPID.unpack(appinfo_file.read(_APPID.size))[0]) != 0:
            (size,) = _SIZE.unpack(appinfo_file.read(_SIZE.size))
            start = appinfo_file.tell()
            entries[appid] = (start + _ENTRY_METADATA_SIZE[magic], start + size)
            appinfo_file.seek(size, os.SEEK_CUR)
    return _AppInfoIndex(path, mtime_ns, key_table, entries)


def _read_oslist(index: _AppInfoIndex, appid: int) -> tuple[str, ...]:
    start, end = index.entries[appid]
    try:
        with open(index.path, "rb") as appinfo_file:
            appinfo_file.seek(start)
            data = vdf.binary_loads(appinfo_file.read(end - start), key_table=index.key_table)
    except (OSError, SyntaxError, IndexError, struct.error) as ex:
        logger.warning("Unable to read app %s from Steam's app cache: %s", appid, ex)
        return ()
    common: Any = data.get("appinfo", {}).get("common", {})
    oslist = common.get("oslist", "") if isinstance(common, dict) else ""
    return tuple(name.strip().casefold() for name in str(oslist).split(",") if name.strip())
