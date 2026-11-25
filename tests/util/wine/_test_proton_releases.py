"""Proton releases downloaded from GitHub."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from qtris.util.wine import proton_releases
from qtris.util.wine.proton_releases import GE_PROTON, PROTON_CACHYOS, PROTON_WINELAND

CACHYOS = "proton-cachyos-11.0-20261015-slr"
WINELAND = "proton-wineland-11.0-20261124"
LEGACY_WINELAND = "proton-cachyos-wineland-11.0-20260713.5-slr"


def make_release(*names, tag=""):
    return {
        "tag_name": tag,
        "assets": [{"name": name, "browser_download_url": "https://dl/" + name} for name in names],
    }


class TestReleaseNames(unittest.TestCase):
    def test_releases(self):
        self.assertIs(proton_releases.get_family("GE-Proton11-7"), GE_PROTON)
        self.assertIs(proton_releases.get_family("GE-Proton9-26"), GE_PROTON)
        self.assertIs(proton_releases.get_family(CACHYOS), PROTON_CACHYOS)
        self.assertIs(proton_releases.get_family("proton-cachyos-9.0-20250126"), PROTON_CACHYOS)
        self.assertIs(proton_releases.get_family(WINELAND), PROTON_WINELAND)
        self.assertIs(proton_releases.get_family(LEGACY_WINELAND), PROTON_WINELAND)

    def test_sentinel_and_other_versions_are_not_releases(self):
        for version in ("ge-proton", "wine-ge-8-26-x86_64", "Proton 11.0", "UMU-Proton-11.0-2e", "", None):
            with self.subTest(version=version):
                self.assertFalse(proton_releases.is_proton_release(version))

    def test_labels(self):
        self.assertEqual(proton_releases.get_release_label("GE-Proton11-7"), "GE-Proton11-7")
        self.assertEqual(proton_releases.get_release_label(CACHYOS), "Proton-CachyOS 11.0-20261015")
        self.assertEqual(proton_releases.get_release_label(WINELAND), "Proton Wineland 11.0-20261124")
        self.assertEqual(proton_releases.get_release_label("Proton 11.0"), "Proton 11.0")

    def test_cachyos_tags(self):
        self.assertEqual(PROTON_CACHYOS.get_tag(CACHYOS), "cachyos-11.0-20261015-slr")
        self.assertEqual(PROTON_CACHYOS.get_release("cachyos-11.0-20261015-slr"), CACHYOS)

    @patch.object(proton_releases.LINUX_SYSTEM, "arch", "aarch64")
    def test_wineland_is_not_offered_on_arm64(self) -> None:
        self.assertEqual(proton_releases.get_family_names(), (GE_PROTON.name, PROTON_CACHYOS.name))

    def test_releases_sort_by_family_then_newest_first(self):
        releases = {"GE-Proton10-9", "proton-cachyos-10.0-20250714-slr", CACHYOS, "GE-Proton11-12", "GE-Proton10-25"}
        self.assertEqual(
            proton_releases.sort_releases(releases),
            ["GE-Proton11-12", "GE-Proton10-25", "GE-Proton10-9", CACHYOS, "proton-cachyos-10.0-20250714-slr"],
        )


@patch.object(proton_releases.LINUX_SYSTEM, "arch", "x86_64")
class TestInstalledBuilds(unittest.TestCase):
    def test_builds_belong_to_their_release(self):
        for build, release in (
            ("GE-Proton11-12-x86_64", "GE-Proton11-12"),
            ("GE-Proton10-25", "GE-Proton10-25"),
            (CACHYOS + "-x86_64", CACHYOS),
            (CACHYOS + "-x86_64_v3", CACHYOS),
            (WINELAND + "-x86_64_v3", WINELAND),
            (LEGACY_WINELAND + "-x86_64", LEGACY_WINELAND),
        ):
            with self.subTest(build=build):
                self.assertEqual(proton_releases.get_release_of_build(build), release)

    def test_builds_for_other_machines_belong_to_no_release(self):
        for build in ("GE-Proton11-12-aarch64", CACHYOS + "-arm64", "Proton 11.0", "Proton-GE Latest"):
            with self.subTest(build=build):
                self.assertIsNone(proton_releases.get_release_of_build(build))

    def test_folder_names(self):
        self.assertEqual(
            proton_releases.get_build_folder_names(CACHYOS),
            (CACHYOS + "-x86_64_v3", CACHYOS + "-x86_64", CACHYOS),
        )
        self.assertEqual(proton_releases.get_build_folder_names("Proton 11.0"), ("Proton 11.0",))

    def test_archive_names_give_their_folder(self):
        self.assertEqual(proton_releases.get_build_name(CACHYOS + "-x86_64.tar.xz"), CACHYOS + "-x86_64")
        self.assertEqual(proton_releases.get_build_name("GE-Proton11-7-x86_64.tar.gz"), "GE-Proton11-7-x86_64")


@patch.object(proton_releases.LINUX_SYSTEM, "arch", "x86_64")
class TestReleaseUrls(unittest.TestCase):
    def get_urls(self, release, data):
        with patch.object(proton_releases, "Request") as request:
            request.return_value.get.return_value.json = data
            urls = proton_releases.get_proton_release_urls(release)
        return urls, request.call_args.args[0] if request.call_args else None

    def test_picks_the_ge_proton_build_for_this_architecture(self):
        data = make_release(
            "GE-Proton11-7-aarch64.sha512sum",
            "GE-Proton11-7-aarch64.tar.gz",
            "GE-Proton11-7-x86_64.sha512sum",
            "GE-Proton11-7-x86_64.tar.gz",
        )
        urls, api_url = self.get_urls("GE-Proton11-7", data)
        self.assertEqual(urls, ("https://dl/GE-Proton11-7-x86_64.tar.gz", "https://dl/GE-Proton11-7-x86_64.sha512sum"))
        self.assertTrue(api_url.endswith("/GloriousEggroll/proton-ge-custom/releases/tags/GE-Proton11-7"))

    def test_older_ge_proton_releases_have_no_arch_suffix(self):
        urls, _api_url = self.get_urls("GE-Proton9-7", make_release("GE-Proton9-7.sha512sum", "GE-Proton9-7.tar.gz"))
        self.assertEqual(urls, ("https://dl/GE-Proton9-7.tar.gz", "https://dl/GE-Proton9-7.sha512sum"))

    def test_no_build_without_a_checksum(self):
        self.assertIsNone(self.get_urls("GE-Proton11-7", make_release("GE-Proton11-7-x86_64.tar.gz"))[0])
        self.assertIsNone(self.get_urls("GE-Proton11-7", {})[0])

    def cachyos_release(self):
        return make_release(
            *(f"{CACHYOS}-{arch}.{ext}" for arch in ("arm64", "x86_64", "x86_64_v3") for ext in ("tar.xz", "sha512sum"))
        )

    @patch.object(proton_releases, "_supports_x86_64_v3", return_value=True)
    def test_cachyos_prefers_the_optimized_build(self, _v3):
        urls, api_url = self.get_urls(CACHYOS, self.cachyos_release())
        self.assertEqual(urls, (f"https://dl/{CACHYOS}-x86_64_v3.tar.xz", f"https://dl/{CACHYOS}-x86_64_v3.sha512sum"))
        self.assertTrue(api_url.endswith("/CachyOS/proton-cachyos/releases/tags/cachyos-11.0-20261015-slr"))

    @patch.object(proton_releases, "_supports_x86_64_v3", return_value=False)
    def test_cachyos_falls_back_to_the_baseline_build(self, _v3):
        urls, _api_url = self.get_urls(CACHYOS, self.cachyos_release())
        self.assertEqual(urls, (f"https://dl/{CACHYOS}-x86_64.tar.xz", f"https://dl/{CACHYOS}-x86_64.sha512sum"))

    def test_wineland_uses_the_compatible_checked_build(self) -> None:
        data = make_release(
            *(
                f"{WINELAND}-{arch}.{ext}"
                for arch in ("x86_64", "x86_64_v3", "x86_64_wow64")
                for ext in ("tar.xz", "sha512sum")
            )
        )
        for supports_v3, arch in ((True, "x86_64_v3"), (False, "x86_64")):
            with (
                self.subTest(arch=arch),
                patch.object(proton_releases, "_supports_x86_64_v3", return_value=supports_v3),
            ):
                urls, api_url = self.get_urls(WINELAND, data)
                self.assertEqual(
                    urls, (f"https://dl/{WINELAND}-{arch}.tar.xz", f"https://dl/{WINELAND}-{arch}.sha512sum")
                )
                self.assertEqual(
                    api_url,
                    "https://api.github.com/repos/nanomatters/proton-salkim/releases/tags/wineland-11.0-20261124",
                )

    @patch.object(proton_releases, "_supports_x86_64_v3", return_value=False)
    def test_release_list_skips_prereleases_and_builds_for_other_machines(self, _v3):
        releases = [
            self.cachyos_release() | {"tag_name": "cachyos-11.0-20261015-slr"},
            make_release(tag="cachyos-11.0-20261014-slr") | {"prerelease": True},
            make_release(f"{CACHYOS}-arm64.tar.xz", f"{CACHYOS}-arm64.sha512sum", tag="cachyos-11.0-20261013-slr"),
        ]
        with patch.object(proton_releases, "Request") as request:
            request.return_value.get.return_value.json = releases
            self.assertEqual(proton_releases.get_proton_releases(PROTON_CACHYOS), (CACHYOS,))
