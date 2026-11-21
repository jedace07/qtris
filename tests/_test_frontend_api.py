"""Keep CLI and Qt on the plain-data backend API."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIRS = {"cli", "qt"}
FRONTEND_API = (
    "qtris.backend",
    "qtris.delegates",
    "qtris.exceptions",
    "qtris.settings",
    "qtris.util.datapath",
    "qtris.util.display_backend",
    "qtris.util.jobs",
    "qtris.util.log",
    "qtris.util.mainloop",
    "qtris.util.notifications",
    "qtris.util.strings",
)


def find_imported_modules(source: str, package: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                base = ".".join([*parts[: len(parts) - node.level + 1], *([base] if base else [])])
            for alias in node.names:
                full_name = f"{base}.{alias.name}"
                path = ROOT.joinpath(*full_name.split("."))
                if full_name == "qtris.gui" or path.with_suffix(".py").is_file() or path.is_dir():
                    found.add(full_name)
                else:
                    found.add(base)
    return found


def is_allowed(module: str, frontend: str) -> bool:
    if not module.startswith("qtris."):
        return module != "qtris"
    allowed = (*FRONTEND_API, f"qtris.{frontend}")
    if frontend == "qt":
        allowed = (*allowed, "qtris.cli")
    return any(module == prefix or module.startswith(prefix + ".") for prefix in allowed)


def test_frontends_use_only_the_backend_api():
    violations: dict[str, set[str]] = {}
    for frontend in FRONTEND_DIRS:
        for path in (ROOT / "qtris" / frontend).rglob("*.py"):
            relative = path.relative_to(ROOT)
            package = ".".join(relative.parts[:-1])
            imports = find_imported_modules(path.read_text(encoding="utf-8"), package)
            if forbidden := {module for module in imports if not is_allowed(module, frontend)}:
                violations[relative.as_posix()] = forbidden
    assert not violations, f"Frontends must use the core through qtris.backend: {violations}"


def test_finds_absolute_and_relative_imports():
    source = """
from qtris import settings
from qtris.game import Game
from .tasks import start_task
from ..backend import games
import qtris.runners
"""
    assert find_imported_modules(source, "qtris.qt") == {
        "qtris.settings",
        "qtris.game",
        "qtris.qt.tasks",
        "qtris.backend.games",
        "qtris.runners",
    }
    assert not is_allowed("qtris.game", "qt")
    assert not is_allowed("qtris.qt.application", "cli")
