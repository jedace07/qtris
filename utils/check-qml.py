"""Build the Python QML module metadata and lint the frontend."""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

from PySide6.QtCore import QLibraryInfo

ROOT = Path(__file__).resolve().parents[1]
QML_DIR = ROOT / "qtris" / "qt" / "qml"
BRIDGE = "QtrisBridge"


def main() -> None:
    subprocess.run(["pyside6-project", "build"], cwd=ROOT, check=True)

    with (ROOT / "pyproject.toml").open("rb") as project:
        files: list[str] = tomllib.load(project)["tool"]["pyside6-project"]["files"]
    inputs = [ROOT / BRIDGE / f"{Path(file).stem}metatypes.json" for file in files if file.endswith(".py")]
    metatypes = [path for path in inputs if path.is_file()]

    imports = ROOT / "build" / "qml"
    module = imports / BRIDGE
    module.mkdir(parents=True, exist_ok=True)
    qt_dir = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.ArchDataPath))
    qt_modules = ("core", "gui", "network", "qml", "quick", "webenginequick")
    foreign = ",".join(str(qt_dir / "metatypes" / f"qt6{name}_metatypes.json") for name in qt_modules)
    # Cross-file property types resolve when the registrar sees the whole module
    subprocess.run(
        [
            "pyside6-qmltyperegistrar",
            "--generate-qmltypes",
            str(module / "plugins.qmltypes"),
            "-o",
            str(module / "registrations.cpp"),
            "--import-name",
            BRIDGE,
            "--major-version",
            "1",
            "--minor-version",
            "0",
            f"--foreign-types={foreign}",
            *map(str, metatypes),
        ],
        cwd=ROOT,
        check=True,
    )
    (module / "qmldir").write_text(f"module {BRIDGE}\ntypeinfo plugins.qmltypes\n", encoding="utf-8")
    subprocess.run(
        [
            "pyside6-qmllint",
            "-W",
            "0",
            "-I",
            str(imports),
            "-I",
            str(QML_DIR),
            "-i",
            str(QML_DIR / "QtrisStyle" / "qmldir"),
            *map(str, sorted(QML_DIR.rglob("*.qml"))),
        ],
        cwd=imports,
        check=True,
    )


if __name__ == "__main__":
    main()
