"""Tests for reading the operating systems of apps from Steam's appinfo.vdf."""

import os
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from qtris.util.steam import appinfo, vdf

V28 = 0x07564428
V29 = 0x07564429


def encode_vdf(data: dict, keys: list[str] | None) -> bytes:
    """Encode a binary VDF; with a key table, as indices into it, as version 29 does."""

    def encode_key(key: str) -> bytes:
        if keys is None:
            return key.encode() + b"\x00"
        if key not in keys:
            keys.append(key)
        return struct.pack("<i", keys.index(key))

    def encode(node: dict) -> bytes:
        out = b""
        for key, value in node.items():
            if isinstance(value, dict):
                out += b"\x00" + encode_key(key) + encode(value)
            elif isinstance(value, int):
                out += b"\x02" + encode_key(key) + struct.pack("<i", value)
            else:
                out += b"\x01" + encode_key(key) + str(value).encode() + b"\x00"
        return out + b"\x08"

    return encode(data)


def build_appinfo(apps: dict[int, dict], version: int, extended: dict[int, dict] | None = None) -> bytes:
    """Build an appinfo.vdf holding the common section, and any extended section, of apps."""
    keys: list[str] | None = [] if version == V29 else None
    entries = b""
    for appid, common in apps.items():
        app = {"appid": appid, "common": common}
        if extended and appid in extended:
            app["extended"] = extended[appid]
        blob = encode_vdf({"appinfo": app}, keys)
        # Info state, last update, access token, two SHA-1s and change number
        metadata = b"\x00" * 60
        entries += struct.pack("<II", appid, len(metadata) + len(blob)) + metadata + blob
    entries += struct.pack("<I", 0)
    if keys is None:
        return struct.pack("<II", version, 1) + entries
    header_size = 16
    key_table = struct.pack("<I", len(keys)) + b"".join(key.encode() + b"\x00" for key in keys)
    return struct.pack("<IIq", version, 1, header_size + len(entries)) + entries + key_table


class AppInfoTest(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.steam_dir = Path(directory.name)
        (self.steam_dir / "appcache").mkdir()
        patcher = patch.object(appinfo, "get_steam_dir", return_value=str(self.steam_dir))
        patcher.start()
        self.addCleanup(patcher.stop)
        appinfo._index = None
        appinfo._apps.clear()

    def write_appinfo(
        self,
        apps: dict[int, dict],
        version: int = V29,
        mtime_ns: int | None = None,
        extended: dict[int, dict] | None = None,
    ) -> None:
        path = self.steam_dir / "appcache" / "appinfo.vdf"
        path.write_bytes(build_appinfo(apps, version, extended))
        if mtime_ns:
            os.utime(path, ns=(mtime_ns, mtime_ns))

    def test_reads_oslists_with_a_key_table(self):
        self.write_appinfo({440: {"name": "TF2", "oslist": "windows,macos,linux"}, 1422450: {"oslist": "windows"}})
        self.assertEqual(appinfo.get_app_oslist("440"), ("windows", "macos", "linux"))
        self.assertEqual(appinfo.get_app_oslist(1422450), ("windows",))

    def test_reads_oslists_with_inline_keys(self):
        self.write_appinfo({730: {"oslist": "windows, linux"}}, version=V28)
        self.assertEqual(appinfo.get_app_oslist("730"), ("windows", "linux"))

    def test_unknown_apps_have_no_oslist(self):
        self.write_appinfo({440: {"name": "TF2"}})
        self.assertEqual(appinfo.get_app_oslist("440"), ())
        self.assertEqual(appinfo.get_app_oslist("12345"), ())
        self.assertEqual(appinfo.get_app_oslist("not-an-appid"), ())

    def test_no_cache_or_an_unknown_version_has_no_oslists(self):
        self.assertEqual(appinfo.get_app_oslist("440"), ())
        self.write_appinfo({440: {"oslist": "linux"}}, version=0x07564420)
        self.assertEqual(appinfo.get_app_oslist("440"), ())

    def test_the_cache_is_read_again_once_steam_rewrites_it(self):
        self.write_appinfo({440: {"oslist": "windows"}}, mtime_ns=1_000_000_000)
        self.assertEqual(appinfo.get_app_oslist("440"), ("windows",))
        self.write_appinfo({440: {"oslist": "windows,linux"}}, mtime_ns=2_000_000_000)
        self.assertEqual(appinfo.get_app_oslist("440"), ("windows", "linux"))

    def test_reads_the_kind_of_app(self):
        self.write_appinfo({3545060: {"type": "Game", "parent": 440}, 2805730: {"type": "Tool"}, 440: {}})
        self.assertEqual(appinfo.get_app_type(3545060), "game")
        self.assertEqual(appinfo.get_app_type("2805730"), "tool")
        self.assertEqual(appinfo.get_app_type(440), "")
        self.assertEqual(appinfo.get_app_type(12345), "")

    def test_reads_the_names_steam_gives_its_compatibility_tools(self):
        compat_tools = {
            "proton_9": {"appid": 2805730, "display_name": "Proton 9.0-4", "from_oslist": "windows"},
            "proton_experimental": {"appid": 1493710, "display_name": "Proton Experimental"},
            "unnamed": {"appid": 4183110},
            "not_a_tool": "text",
        }
        self.write_appinfo(
            {appinfo.STEAMPLAY_MANIFESTS_APPID: {"type": "Config"}},
            extended={appinfo.STEAMPLAY_MANIFESTS_APPID: {"compat_tools": compat_tools}},
        )
        self.assertEqual(appinfo.get_compat_tool_names(), {2805730: "Proton 9.0-4", 1493710: "Proton Experimental"})

    def test_no_compatibility_tools_without_their_app(self):
        self.write_appinfo({440: {"type": "game"}})
        self.assertEqual(appinfo.get_compat_tool_names(), {})


class BinaryKeyTableTest(TestCase):
    def test_keys_are_looked_up_in_the_key_table(self):
        keys: list[str] = []
        blob = encode_vdf({"common": {"oslist": "linux", "type": "Game"}}, keys)
        self.assertEqual(vdf.binary_loads(blob, key_table=keys), {"common": {"oslist": "linux", "type": "Game"}})
