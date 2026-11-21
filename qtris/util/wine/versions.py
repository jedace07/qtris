"""Wine version names, which end with the architecture they run on."""

from __future__ import annotations

from qtris.util.linux import LINUX_SYSTEM


def normalize_version_architecture(version_name: str) -> str:
    """Ensures the version name has an architecture on the end ofit;
    adds the system's architecture if it is missing."""
    base_version, arch = parse_version_architecture(version_name)
    return format_version_architecture(base_version, arch) if base_version else ""


def format_version_architecture(base_version: str, arch: str | None = None) -> str:
    """Assembles a version with architecture from the version and architecture."""
    if not base_version:
        return ""

    # A gross hack, since runner versions could be used with non-Wine runners,
    # but it so happens we don't. 'GE-Proton' versions arbitrarily do not have
    # an architecture on them - they are always 64-bit. This also covers the
    # lower-case 'ge-proton' sentinel meaning "whatever GE-Proton Umu provides";
    # suffixing that yields a version that resolves to no Wine at all.
    if base_version.casefold().startswith("ge-proton"):
        return base_version

    if arch:
        return "{}-{}".format(base_version, arch)

    return base_version


def parse_version_architecture(version_name: str) -> tuple[str, str | None]:
    """Splits a version that ends with an architecture into the plain version and
    architecture, as a tuple. If the version has no architecture, this provides
    the system's architecture instead."""
    if version_name:
        if version_name.endswith("-i386") or version_name.endswith("-x86_64"):
            version, arch = version_name.rsplit("-", 1)
            return version, arch
    return version_name, LINUX_SYSTEM.arch
