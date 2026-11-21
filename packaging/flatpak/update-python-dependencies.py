"""Generate the Flatpak module that installs Qtris's Python dependencies.

Every package comes from uv.lock, with the URL and hash it pins, so the Flatpak
installs the same versions the test suite runs against without network access
during the build. A wheel is used where uv.lock has one the runtime can load,
otherwise the source distribution is built against the SDK.

Run it after changing uv.lock, and commit the result::

    uv run --no-project packaging/flatpak/update-python-dependencies.py

With ``--check`` it only reports whether python-dependencies.json is current.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, TypedDict, cast

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).with_name("python-dependencies.json")

# The runtime's Python, architecture and glibc (org.freedesktop.Platform 26.08)
PYTHON = (3, 14)
ARCH = "x86_64"
GLIBC = (2, 44)

# Installed first, because building another dependency needs them
BUILD_FIRST = ("pycairo",)

# pip arguments shared by every install: only the files listed as sources, built
# with the backends from the flatpak-build dependency group
PIP_INSTALL = 'pip3 install --no-index --find-links="file://${PWD}" --no-build-isolation'
BACKENDS = "${PWD}/build-backends"
# Makes the backends, and tools they run such as patchelf, available to builds
WITH_BACKENDS = f'PYTHONPATH={BACKENDS} PATH="{BACKENDS}/bin:$PATH"'

WHEEL_NAME = re.compile(r"^[^-]+-[^-]+(?:-\d[^-]*)?-(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>[^-]+)\.whl$")
MANYLINUX = re.compile(r"^manylinux_(?P<major>\d+)_(?P<minor>\d+)_(?P<arch>.+)$")
LEGACY_MANYLINUX = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}


class LockedFile(TypedDict):
    """A wheel or sdist in uv.lock."""

    url: str
    hash: str


class LockedPackage(TypedDict):
    """A package in uv.lock, as far as this script reads it."""

    name: str
    version: NotRequired[str]
    sdist: NotRequired[LockedFile]
    wheels: NotRequired[list[LockedFile]]


def export(*args: str) -> list[tuple[str, str]]:
    """Return the ``(name, version)`` pins uv exports from uv.lock.

    Parameters
    ----------
    *args : str
        Arguments selecting the dependencies to export.

    Returns
    -------
    list of tuple of str
        Normalized package names and their locked versions.
    """
    result = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-hashes",
            "--no-header",
            "--no-annotate",
            "--format",
            "requirements.txt",
            *args,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    pins: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ";" in line:
            # Not needed so far; evaluating markers would need the packaging library
            raise SystemExit(f"Environment markers are not supported: {line}")
        name, separator, version = line.partition("==")
        if not separator:
            raise SystemExit(f"Not a pinned requirement: {line}")
        pins.append((name, version))
    return pins


def platform_supported(platform: str) -> bool:
    """Return whether a wheel's platform tag can run on the runtime.

    Parameters
    ----------
    platform : str
        One platform tag, such as ``manylinux_2_28_x86_64``.

    Returns
    -------
    bool
        True for ``any`` and for x86_64 manylinux tags the runtime's glibc covers.
    """
    if platform == "any":
        return True
    for legacy, glibc in LEGACY_MANYLINUX.items():
        if platform == f"{legacy}_{ARCH}":
            return glibc <= GLIBC
    match = MANYLINUX.match(platform)
    if match is None or match["arch"] != ARCH:
        return False
    return (int(match["major"]), int(match["minor"])) <= GLIBC


def python_supported(python: str, abi: str) -> bool:
    """Return whether a wheel's Python and ABI tags match the runtime's Python.

    Parameters
    ----------
    python : str
        The Python tag, such as ``py3`` or ``cp310``.
    abi : str
        The ABI tag, such as ``none``, ``abi3`` or ``cp314``.

    Returns
    -------
    bool
        True when the runtime's CPython can load the wheel.
    """
    cpython = f"cp{PYTHON[0]}{PYTHON[1]}"
    for tag in python.split("."):
        if abi == "none" and tag in {"py3", f"py{PYTHON[0]}{PYTHON[1]}", cpython}:
            return True
        if abi == cpython and tag == cpython:
            return True
        if abi == "abi3" and tag.startswith("cp3") and int(tag[3:]) <= PYTHON[1]:
            return True
    return False


def wheel_supported(filename: str) -> bool:
    """Return whether the runtime can install a wheel.

    Parameters
    ----------
    filename : str
        The wheel's file name.

    Returns
    -------
    bool
        True when one of its tag combinations fits the runtime.
    """
    match = WHEEL_NAME.match(filename)
    if match is None:
        return False
    abis = match["abi"].split(".")
    return any(python_supported(match["python"], abi) for abi in abis) and any(
        platform_supported(platform) for platform in match["platform"].split(".")
    )


def source(package: LockedPackage) -> dict[str, str]:
    """Return the flatpak-builder source of one locked package.

    Parameters
    ----------
    package : LockedPackage
        The package's entry in uv.lock.

    Returns
    -------
    dict of str
        A ``file`` source for a supported wheel, or else for the sdist.
    """
    wheels = package.get("wheels", [])
    chosen = next((wheel for wheel in wheels if wheel_supported(wheel["url"].rsplit("/", 1)[1])), None)
    if chosen is None:
        chosen = package.get("sdist")
    if chosen is None:
        raise SystemExit(f"No usable wheel or sdist for {package['name']}")
    algorithm, _, digest = chosen["hash"].partition(":")
    if algorithm != "sha256":
        raise SystemExit(f"Unexpected {algorithm} hash for {package['name']}")
    return {"type": "file", "url": chosen["url"], "sha256": digest}


def requirements(pins: Iterable[tuple[str, str]]) -> str:
    """Return pins as pip command-line requirements.

    Parameters
    ----------
    pins : iterable of tuple of str
        Package names and versions.

    Returns
    -------
    str
        The requirements, space separated.
    """
    return " ".join(f"{name}=={version}" for name, version in pins)


def generate() -> str:
    """Return the module, serialized as it should be written.

    Returns
    -------
    str
        The JSON text of python-dependencies.json.
    """
    with (ROOT / "uv.lock").open("rb") as lock_file:
        locked = cast("list[LockedPackage]", tomllib.load(lock_file)["package"])
    packages = {(package["name"], package["version"]): package for package in locked if "version" in package}

    runtime = export("--no-dev", "--no-emit-project")
    backends = export("--only-group", "flatpak-build")
    first = [pin for pin in runtime if pin[0] in BUILD_FIRST]
    rest = [pin for pin in runtime if pin[0] not in BUILD_FIRST]
    if len(first) != len(BUILD_FIRST):
        raise SystemExit(f"Not all of {', '.join(BUILD_FIRST)} are runtime dependencies")

    sources: list[dict[str, str]] = []
    for pin in dict.fromkeys([*runtime, *backends]):
        if pin not in packages:
            raise SystemExit(f"{pin[0]} {pin[1]} is missing from uv.lock")
        sources.append(source(packages[pin]))
    log.info("%d packages, %d built from source", len(sources), sum(not s["url"].endswith(".whl") for s in sources))

    module = {
        "name": "python3-dependencies",
        "buildsystem": "simple",
        "build-commands": [
            # The build backends go in a scratch directory, not the app
            f"{PIP_INSTALL} --target={BACKENDS} {requirements(backends)}",
            f"{WITH_BACKENDS} {PIP_INSTALL} --prefix=${{FLATPAK_DEST}} --no-deps {requirements(first)}",
            # The exact locked set: --ignore-installed so packages that are also build
            # backends' dependencies still get installed in the app
            f"{WITH_BACKENDS} {PIP_INSTALL} --prefix=${{FLATPAK_DEST}} --no-deps --ignore-installed "
            f"{requirements(rest)}",
        ],
        "sources": sources,
    }
    # flatpak-builder ignores properties starting with x-
    header = {"x-generated-by": "packaging/flatpak/update-python-dependencies.py, from uv.lock; do not edit"}
    return json.dumps(header | module, indent=4) + "\n"


def main() -> int:
    """Write python-dependencies.json, or check that it's current.

    Returns
    -------
    int
        The exit status.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date, without writing it")
    arguments = parser.parse_args()

    content = generate()
    current = OUTPUT.read_text() if OUTPUT.exists() else None
    if arguments.check:
        if content != current:
            log.error("%s is out of date; run %s", OUTPUT.relative_to(ROOT), Path(__file__).relative_to(ROOT))
            return 1
        log.info("%s is up to date", OUTPUT.relative_to(ROOT))
        return 0
    if content != current:
        OUTPUT.write_text(content)
        log.info("Wrote %s", OUTPUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
