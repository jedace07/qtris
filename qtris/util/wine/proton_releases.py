"""Proton builds Qtris downloads from their upstream GitHub releases: GE-Proton and
Proton-CachyOS.

A release is stored in configurations under its release name, such as
'GE-Proton11-12' or 'proton-cachyos-11.0-20261015-slr'. Its builds unpack into
folders named after the release and the architecture they were built for, such as
'GE-Proton11-12-x86_64' or 'proton-cachyos-11.0-20261015-slr-x86_64_v3'.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from qtris.util.http import Request
from qtris.util.linux import LINUX_SYSTEM
from qtris.util.log import logger
from qtris.util.strings import get_natural_sort_key

if TYPE_CHECKING:
    from collections.abc import Callable

GITHUB_API_URL = "https://api.github.com/repos"
ARCHIVE_SUFFIXES = (".tar.gz", ".tar.xz")

# The CPU features of the x86-64-v3 microarchitecture level, as /proc/cpuinfo names them
_X86_64_V3_FLAGS = frozenset(
    ("avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe", "xsave")
    + ("cx16", "lahf_lm", "popcnt", "sse4_1", "sse4_2", "ssse3")
)


@dataclass(frozen=True)
class ProtonFamily:
    """A Proton fork that publishes its builds as GitHub releases."""

    name: str
    repository: str
    release_pattern: re.Pattern[str]
    get_tag: Callable[[str], str]
    get_release: Callable[[str], str]
    get_label: Callable[[str], str]
    # Folder suffixes for each architecture, the build to install first
    arch_suffixes: dict[str, tuple[str, ...]]
    # Older x86_64 builds had no architecture in their names
    unsuffixed_x86_64: bool = False

    def is_release(self, version: str) -> bool:
        return self.release_pattern.fullmatch(version) is not None

    def get_build_suffixes(self) -> tuple[str, ...]:
        """Return the folder suffixes of the builds this machine can run, preferred first."""
        suffixes = self.arch_suffixes.get(LINUX_SYSTEM.arch or "", ())
        if LINUX_SYSTEM.arch == "x86_64":
            suffixes = tuple(suffix for suffix in suffixes if suffix != "-x86_64_v3" or _supports_x86_64_v3())
        return (*suffixes, "") if self.unsuffixed_x86_64 and LINUX_SYSTEM.arch == "x86_64" else suffixes

    def get_folder_suffixes(self) -> tuple[str, ...]:
        """Return the suffixes of folders a build for this architecture may be found in."""
        return (*self.arch_suffixes.get(LINUX_SYSTEM.arch or "", ()), "")


GE_PROTON = ProtonFamily(
    name="GE-Proton",
    repository="GloriousEggroll/proton-ge-custom",
    release_pattern=re.compile(r"GE-Proton\d+-\d+"),
    get_tag=lambda release: release,
    get_release=lambda tag: tag,
    get_label=lambda release: release,
    arch_suffixes={"x86_64": ("-x86_64",), "aarch64": ("-aarch64",)},
    unsuffixed_x86_64=True,
)

_CACHYOS_RELEASE = re.compile(r"proton-cachyos-(\d+(?:\.\d+)*-\d+)(?:-slr)?")


def _get_cachyos_label(release: str) -> str:
    match = _CACHYOS_RELEASE.fullmatch(release)
    return "Proton-CachyOS " + match.group(1) if match else release


PROTON_CACHYOS = ProtonFamily(
    name="Proton-CachyOS",
    repository="CachyOS/proton-cachyos",
    release_pattern=_CACHYOS_RELEASE,
    get_tag=lambda release: release.removeprefix("proton-"),
    get_release=lambda tag: "proton-" + tag,
    get_label=_get_cachyos_label,
    arch_suffixes={"x86_64": ("-x86_64_v3", "-x86_64"), "aarch64": ("-arm64",)},
)

FAMILIES: tuple[ProtonFamily, ...] = (GE_PROTON, PROTON_CACHYOS)


def get_family(version: str | None) -> ProtonFamily | None:
    """Return the family a release name belongs to, or None if it names no release."""
    if version:
        for family in FAMILIES:
            if family.is_release(version):
                return family
    return None


def get_family_by_name(name: str) -> ProtonFamily:
    for family in FAMILIES:
        if family.name == name:
            return family
    raise ValueError("Unknown Proton family: %s" % name)


def is_proton_release(version: str | None) -> bool:
    """Check whether a version names a specific GE-Proton or Proton-CachyOS release."""
    return get_family(version) is not None


def get_release_label(release: str) -> str:
    """Return how a release is shown to the user."""
    family = get_family(release)
    return family.get_label(release) if family else release


def get_release_of_build(build_name: str) -> str | None:
    """Return the release an installed build for this architecture belongs to, or None
    if the folder holds no GE-Proton or Proton-CachyOS build this machine can run."""
    for family in FAMILIES:
        for suffix in family.get_folder_suffixes():
            if (not suffix or build_name.endswith(suffix)) and family.is_release(
                release := build_name.removesuffix(suffix)
            ):
                return release
    return None


def get_build_folder_names(release: str) -> tuple[str, ...]:
    """Return the folder names a release's build for this architecture may have."""
    family = get_family(release)
    if not family:
        return (release,)
    return tuple(release + suffix for suffix in family.get_folder_suffixes())


def sort_releases(releases: set[str] | list[str] | tuple[str, ...]) -> list[str]:
    """Sort releases by family, GE-Proton first, then newest first."""

    def family_index(release: str) -> int:
        family = get_family(release)
        return FAMILIES.index(family) if family else len(FAMILIES)

    newest_first = sorted(releases, key=get_natural_sort_key, reverse=True)
    return sorted(newest_first, key=family_index)


def get_proton_release_urls(release: str) -> tuple[str, str] | None:
    """Return the URLs of the archive and SHA-512 checksum of a release's build for
    this machine, or None if the release has no build for it."""
    family = get_family(release)
    if not family:
        return None
    data = Request(f"{GITHUB_API_URL}/{family.repository}/releases/tags/{family.get_tag(release)}").get().json
    return _get_build_urls(family, release, data)


def get_proton_releases(family: ProtonFamily) -> tuple[str, ...]:
    """Return a family's recent releases that have a build for this machine."""
    data = Request(f"{GITHUB_API_URL}/{family.repository}/releases?per_page=30").get().json
    if not isinstance(data, list):
        raise ValueError("Expected a list of %s releases" % family.name)
    releases = []
    for item in data:
        if item.get("draft") or item.get("prerelease"):
            continue
        release = family.get_release(item.get("tag_name") or "")
        if family.is_release(release) and _get_build_urls(family, release, item):
            releases.append(release)
    return tuple(releases)


def _get_build_urls(family: ProtonFamily, release: str, data: dict[str, Any]) -> tuple[str, str] | None:
    assets = {asset["name"]: asset["browser_download_url"] for asset in data.get("assets", [])}
    for suffix in family.get_build_suffixes():
        build_name = release + suffix
        checksum_url = assets.get(build_name + ".sha512sum")
        for archive_suffix in ARCHIVE_SUFFIXES:
            archive_url = assets.get(build_name + archive_suffix)
            if archive_url and checksum_url:
                return archive_url, checksum_url
    return None


def get_build_name(archive_name: str) -> str:
    """Return the folder a build's archive unpacks to, which is named after it."""
    for archive_suffix in ARCHIVE_SUFFIXES:
        if archive_name.endswith(archive_suffix):
            return archive_name.removesuffix(archive_suffix)
    return archive_name


def get_proton_checksum(checksum_url: str) -> str:
    """Return the SHA-512 digest from a Proton '.sha512sum' file, which is in
    sha512sum's '<digest>  <file name>' format, or "" if the file is empty."""
    fields = Request(checksum_url).get().text.split()
    return fields[0] if fields else ""


@cache
def _supports_x86_64_v3() -> bool:
    """Check whether the CPU supports the x86-64-v3 level, which Proton-CachyOS has
    optimized builds for."""
    try:
        cpus = LINUX_SYSTEM.get_cpus()
    except OSError as ex:
        logger.warning("Unable to read the CPU features: %s", ex)
        return False
    flags = set(cpus[0].get("flags", "").split()) if cpus else set()
    return _X86_64_V3_FLAGS <= flags


def get_family_names() -> tuple[str, ...]:
    return tuple(family.name for family in FAMILIES)
