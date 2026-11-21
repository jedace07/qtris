"""Every core module can be imported first.

Tests import modules in whatever order they happen to, which hides circular
imports that break a module imported on its own (as the launcher and scripts do).
"""

import subprocess
import sys
from pathlib import Path

QTRIS_DIR = Path(__file__).resolve().parents[1] / "qtris"

# Imports each module named on the command line with no qtris module loaded,
# and prints those that fail
IMPORT_EACH = """
import importlib
import sys
import traceback

for name in sys.argv[1:]:
    for loaded in [m for m in sys.modules if m == "qtris" or m.startswith("qtris.")]:
        del sys.modules[loaded]
    try:
        importlib.import_module(name)
    except Exception:
        print("%s:\\n%s" % (name, traceback.format_exc(limit=-3)))
"""


def test_core_modules_import_on_their_own():
    names = sorted(f"qtris.{path.stem}" for path in QTRIS_DIR.glob("*.py") if path.stem != "__init__")
    names += ["qtris.services", "qtris.installer", "qtris.runners"]
    result = subprocess.run(
        [sys.executable, "-c", IMPORT_EACH, *names],
        capture_output=True,
        text=True,
        check=True,
        cwd=QTRIS_DIR.parent,
    )
    assert not result.stdout, result.stdout
