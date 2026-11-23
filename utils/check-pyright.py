"""Run Pyright and fail on errors beyond the checked-in baseline."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import NotRequired, TypedDict

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "pyright-baseline.json"
log = logging.getLogger(__name__)


class Position(TypedDict):
    line: int
    character: int


class DiagnosticRange(TypedDict):
    start: Position


class Diagnostic(TypedDict):
    file: str
    severity: str
    message: str
    range: DiagnosticRange
    rule: NotRequired[str]


def report_error(path: str, diagnostic: Diagnostic) -> None:
    start = diagnostic["range"]["start"]
    line, column = start["line"] + 1, start["character"] + 1
    message = f"{diagnostic['message']} [{diagnostic.get('rule', 'pyright')}]"
    if os.environ.get("GITHUB_ACTIONS") == "true":
        message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        log.error("::error file=%s,line=%d,col=%d::%s", path, line, column, message)
    else:
        log.error("%s:%d:%d: %s", path, line, column, message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-baseline", action="store_true", help="Record all current errors as the baseline")
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

    result = subprocess.run(
        ["pyright", "--pythonpath", sys.executable, "--outputjson"], cwd=ROOT, stdout=subprocess.PIPE, text=True
    )
    if result.returncode not in (0, 1):
        log.error("Pyright failed with exit code %d\n%s", result.returncode, result.stdout)
        return 1

    diagnostics: list[Diagnostic] = json.loads(result.stdout)["generalDiagnostics"]
    baseline: dict[str, dict[str, int]] = {}
    if not options.write_baseline:
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    errors: dict[str, Counter[str]] = defaultdict(Counter)
    new_errors = 0
    for diagnostic in diagnostics:
        if diagnostic["severity"] != "error":
            continue
        path = Path(diagnostic["file"]).relative_to(ROOT).as_posix()
        signature = f"{diagnostic.get('rule', 'pyright')}: {diagnostic['message']}"
        errors[path][signature] += 1
        if not options.write_baseline and errors[path][signature] > baseline.get(path, {}).get(signature, 0):
            report_error(path, diagnostic)
            new_errors += 1

    error_count = sum(counter.total() for counter in errors.values())
    if result.returncode == 1 and not error_count:
        log.error("Pyright exited with an error but returned no error diagnostics")
        return 1
    if options.write_baseline:
        BASELINE.write_text(json.dumps(errors, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        log.info("Recorded %d existing errors in %s", error_count, BASELINE.name)
        return 0

    warnings = sum(diagnostic["severity"] == "warning" for diagnostic in diagnostics)
    log.info(
        "Pyright reported %d new errors, %d baseline errors and %d warnings",
        new_errors,
        error_count - new_errors,
        warnings,
    )
    return int(new_errors > 0)


if __name__ == "__main__":
    raise SystemExit(main())
