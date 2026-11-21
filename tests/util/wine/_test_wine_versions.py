"""Tests for the list of Proton versions offered in the Wine runner's version picker."""

import unittest
from unittest.mock import patch

from qtris.util.wine import proton, wine
from qtris.util.wine.wine import GE_PROTON_LATEST, get_installed_wine_versions

INSTALLED = {
    "Proton 9.0 (Beta)": "/steam/common/Proton 9.0 (Beta)",
    "Proton-GE Latest": "/compat/Proton-GE Latest",
    "GE-Proton10-9": "/compat/GE-Proton10-9",
    "Proton 11.0": "/steam/common/Proton 11.0",
    "Proton Hotfix": "/steam/common/Proton Hotfix",
    "GE-Proton11-12-x86_64": "/compat/GE-Proton11-12-x86_64",
    "Proton - Experimental": "/steam/common/Proton - Experimental",
    "GE-Proton10-25": "/compat/GE-Proton10-25",
    "Proton 10.0": "/steam/common/Proton 10.0",
    "proton-cachyos-11.0-20261015-slr-x86_64_v3": "/compat/proton-cachyos-11.0-20261015-slr-x86_64_v3",
}


@patch.object(proton.LINUX_SYSTEM, "arch", "x86_64")
@patch.object(proton, "get_proton_versions", return_value=INSTALLED)
class TestInstalledWineVersions(unittest.TestCase):
    def get_versions(self):
        get_installed_wine_versions.cache_clear()
        try:
            return get_installed_wine_versions()
        finally:
            get_installed_wine_versions.cache_clear()

    def test_only_proton_is_offered_in_presentation_order(self, _versions):
        self.assertEqual(
            self.get_versions(),
            [
                GE_PROTON_LATEST,
                "proton-cachyos-latest",
                "GE-Proton11-12",
                "GE-Proton10-25",
                "GE-Proton10-9",
                "proton-cachyos-11.0-20261015-slr",
                "Proton - Experimental",
                "Proton Hotfix",
                "Proton 11.0",
                "Proton 10.0",
                "Proton 9.0 (Beta)",
                "Proton-GE Latest",
            ],
        )

    def test_a_release_installed_twice_is_listed_once(self, _versions):
        installed = dict(INSTALLED, **{"GE-Proton11-12": "/steam/compat/GE-Proton11-12"})
        with patch.object(proton, "get_proton_versions", return_value=installed):
            self.assertEqual(self.get_versions().count("GE-Proton11-12"), 1)

    def test_ge_proton_latest_has_no_marker_in_its_label(self, _versions):
        self.assertEqual(wine.get_wine_version_label(GE_PROTON_LATEST), "GE-Proton Latest")
        self.assertEqual(wine.get_wine_version_label("GE-Proton11-12"), "GE-Proton11-12")
        self.assertEqual(
            wine.get_wine_version_label("proton-cachyos-11.0-20261015-slr"), "Proton-CachyOS 11.0-20261015"
        )


@patch.object(proton.LINUX_SYSTEM, "arch", "x86_64")
@patch.object(proton, "get_proton_versions", return_value=INSTALLED)
class TestProtonVersionOrDefault(unittest.TestCase):
    def test_ge_proton_builds_are_named_by_release_tag(self, _versions):
        self.assertEqual(wine.get_proton_version_or_default("GE-Proton11-12-x86_64"), "GE-Proton11-12")
        self.assertEqual(wine.get_proton_version_or_default("GE-Proton11-7"), "GE-Proton11-7")

    def test_installed_proton_is_kept(self, _versions):
        self.assertEqual(wine.get_proton_version_or_default("Proton 11.0"), "Proton 11.0")

    def test_anything_else_is_ge_proton_latest(self, _versions):
        for version in (None, "", "system", "custom", "lutris-7.2-2-x86_64", "Proton 8.0", GE_PROTON_LATEST):
            with self.subTest(version=version):
                self.assertEqual(wine.get_proton_version_or_default(version), GE_PROTON_LATEST)

    def test_rolling_builds_are_kept_even_before_they_are_installed(self, _versions):
        self.assertEqual(wine.get_proton_version_or_default("proton-cachyos-latest"), "proton-cachyos-latest")
