"""Remove the PySide6 add-on files Qtris never loads from a Flatpak build.

The store sign-in web view needs QtWebEngine, which comes in the pyside6-webengine
wheel and depends on pyside6-addons, a wheel of every other optional Qt module (3D,
charts, multimedia, Bluetooth and many more). This keeps what Qtris can reach:

* every file from pyside6-essentials, which Qtris needed before the add-ons
* the Python bindings Qtris's code imports and the QML modules its QML imports,
  with the QML modules those depend on
* WebEngine's helper process, resources and translations
* every library any of those link to, and the binding of each library kept

Everything else from the add-on wheels is removed. It runs after Qtris itself is
installed, so it reads Qtris's imports from the installed package::

    python3 packaging/flatpak/trim-pyside6.py /app
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("trim-pyside6")

ESSENTIALS = "pyside6_essentials"
ADD_ONS = ("pyside6_addons", "pyside6_webengine")
PYTHON_IMPORT = re.compile(r"\bPySide6\.(Qt\w+)|\bfrom PySide6 import ([\w ,]+)")
QML_IMPORT = re.compile(r"^\s*import\s+(Qt[\w.]*)", re.MULTILINE)
QMLDIR_IMPORT = ("depends", "import")
# QML modules a module loads at run time without declaring them in its qmldir
RUNTIME_QML = {"QtWebEngine": ("QtWebEngine/ControlsDelegates",)}
WEB_ENGINE_FILES = (
    "PySide6/Qt/libexec/QtWebEngineProcess",
    "PySide6/Qt/resources/",
    "PySide6/Qt/translations/qtwebengine",
)
NEEDED = re.compile(r"\(NEEDED\).*\[(.+?)\]")


def read_record(site: Path, distribution: str) -> set[str]:
    """Return the PySide6 files a wheel installed."""
    info = next(site.glob(f"{distribution}-*.dist-info"))
    with (info / "RECORD").open(encoding="utf-8", newline="") as record:
        return {row[0] for row in csv.reader(record) if row and row[0].startswith("PySide6/")}


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as file:
            return file.read(4) == b"\x7fELF"
    except OSError:
        return False


def needed_libraries(path: Path) -> list[str]:
    output = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True, check=True).stdout
    return NEEDED.findall(output)


def qtris_imports(site: Path) -> tuple[set[str], set[str]]:
    """Return the PySide6 modules Qtris's Python imports and the QML modules its QML imports."""
    python_modules: set[str] = set()
    qml_modules: set[str] = set()
    package = site / "qtris"
    for path in package.rglob("*.py"):
        for dotted, listed in PYTHON_IMPORT.findall(path.read_text(encoding="utf-8")):
            python_modules |= {dotted} if dotted else {name.strip() for name in listed.split(",") if name.strip()}
    for path in package.rglob("*.qml"):
        qml_modules |= set(QML_IMPORT.findall(path.read_text(encoding="utf-8")))
    for path in package.rglob("qmldir"):
        qml_modules |= {name for name in qmldir_imports(path) if name.startswith("Qt")}
    return python_modules, qml_modules


def qmldir_imports(path: Path) -> list[str]:
    """Return the modules a qmldir file depends on or imports."""
    found: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        words = line.split()
        while words and words[0] in ("optional", "default"):
            words = words[1:]
        if len(words) > 1 and words[0] in QMLDIR_IMPORT:
            found.append(words[1])
    return found


def qml_module_files(site: Path, files: set[str], modules: set[str]) -> set[str]:
    """Return the files of the QML modules given and of those they depend on."""
    qml = site / "PySide6/Qt/qml"
    pending = [module.replace(".", "/") for module in modules]
    seen: set[str] = set()
    found: set[str] = set()
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        pending.extend(RUNTIME_QML.get(module, ()))
        prefix = f"PySide6/Qt/qml/{module}/"
        found |= {path for path in files if path.startswith(prefix) and "/" not in path[len(prefix) :]}
        qmldir = qml / module / "qmldir"
        if qmldir.is_file():
            pending.extend(name.replace(".", "/") for name in qmldir_imports(qmldir))
    return found


def removable_files(site: Path) -> list[str]:
    essentials = read_record(site, ESSENTIALS)
    add_ons = set().union(*(read_record(site, distribution) for distribution in ADD_ONS))
    files = essentials | add_ons
    libraries = {Path(path).name: path for path in files if path.startswith("PySide6/Qt/lib/")}
    add_on_libraries = {Path(path).name for path in add_ons if path.startswith("PySide6/Qt/lib/")}

    python_modules, qml_modules = qtris_imports(site)
    log.info("Qtris imports %s and the QML modules %s", sorted(python_modules), sorted(qml_modules))
    roots = {path for path in files if any(path.startswith(f"PySide6/{module}.") for module in python_modules)}
    roots |= qml_module_files(site, files, qml_modules)
    roots |= {path for path in files if path.startswith(WEB_ENGINE_FILES)}
    # A few essentials plugins (3D scenes, the virtual keyboard) link to add-on
    # libraries; they could never load before the add-ons came, so they don't count
    for path in essentials:
        if not is_elf(site / path) or not set(needed_libraries(site / path)) & add_on_libraries:
            roots.add(path)

    kept: set[str] = set()
    pending = list(roots)
    while pending:
        path = pending.pop()
        if path in kept:
            continue
        kept.add(path)
        if is_elf(site / path):
            pending.extend(libraries[name] for name in needed_libraries(site / path) if name in libraries)
            # A kept library's Python binding, which other bindings import
            if match := re.fullmatch(r"PySide6/Qt/lib/libQt6(\w+)\.so[.\d]*", path):
                pending.extend(binding for binding in files if binding.startswith(f"PySide6/Qt{match[1]}."))
    return sorted(add_ons - kept)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prefix", type=Path, help="the installation prefix, such as /app")
    arguments = parser.parse_args()
    site = next(arguments.prefix.glob("lib/python3*/site-packages"))
    removed = 0
    for path in removable_files(site):
        target = site / path
        if target.is_file() or target.is_symlink():
            removed += target.lstat().st_size
            target.unlink()
    for directory in sorted((site / "PySide6").rglob("*"), key=lambda path: len(path.parts), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    log.info("Removed %d MiB of PySide6 add-ons Qtris doesn't use", removed // 2**20)


if __name__ == "__main__":
    main()
