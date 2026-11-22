"""Stand-ins for legendary and Nile that answer like the real clients, so tests run
the real subprocess, JSON and progress handling without an account."""

import sys
from pathlib import Path

import pytest

from qtris import settings
from qtris.util import legendary, nile
from qtris.util.store_cli import StoreClient

_COMMON = """
import json, os, sys
args = sys.argv[1:]
if args and args[0] == "-y":
    args = args[1:]
def option(name):
    return args[args.index(name) + 1] if name in args else None
def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except OSError:
        return default
def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)
def progress():
    for percent in (25, 100):
        print(f"[DLManager] INFO: = Progress: {percent}.00% (1/4), Running for 00:00:01, ETA: 00:00:03",
              file=sys.stderr, flush=True)
    print(" + Download\\t- 12.50 MiB/s (raw) / 20.00 MiB/s (decompressed)", file=sys.stderr, flush=True)
"""

LEGENDARY = (
    _COMMON
    + """
config = os.environ["LEGENDARY_CONFIG_PATH"]
installed_path = os.path.join(config, "installed.json")
installed = load(installed_path, {})
command = args[0]
if command == "auth" and "--delete" in args:
    os.remove(os.path.join(config, "user.json"))
elif command == "auth":
    if option("--token") != "epic-code":
        # The real legendary exits successfully here
        print("[cli] ERROR: Login attempt failed, please see log for details.", file=sys.stderr)
        sys.exit(0)
    save(os.path.join(config, "user.json"), {"displayName": "Tester"})
elif command == "list":
    print(json.dumps([{"app_name": "Sugar", "app_title": "Sugar Rush", "metadata": {
        "id": "catalog-id", "namespace": "ns",
        "keyImages": [{"type": "DieselGameBoxTall", "url": "https://cdn.example/tall.jpg"}],
        "catalogNs": {"mappings": [{"pageType": "productHome", "pageSlug": "sugar-rush"}]}}}]))
elif command == "install":
    if "--update-only" in args:
        installed[args[1]]["version"] = "2"
    else:
        folder = option("--game-folder")
        os.makedirs(os.path.join(folder, "Binaries"), exist_ok=True)
        open(os.path.join(folder, "Binaries", "Sugar.exe"), "w").close()
        installed[args[1]] = {"app_name": args[1], "install_path": folder, "executable": "Binaries/Sugar.exe",
                              "title": "Sugar Rush", "version": "1"}
    progress()
    save(installed_path, installed)
elif command == "list-installed":
    print(json.dumps(list(installed.values())))
elif command == "launch":
    game = installed[args[1]]
    assert "--no-wine" in args and "--json" in args
    print(json.dumps({"game_executable": game["executable"], "game_directory": game["install_path"],
                      "game_parameters": ["-nosplash"], "egl_parameters": ["-AUTH_PASSWORD=one-time", "-epicapp=Sugar"],
                      "working_directory": os.path.join(game["install_path"], "Binaries"),
                      "environment": {"EOS_OVERLAY": "0"}}))
elif command == "uninstall":
    assert "--keep-files" in args
    installed.pop(args[1])
    save(installed_path, installed)
"""
)

NILE = (
    _COMMON
    + """
config = os.path.join(os.environ["NILE_CONFIG_PATH"], "nile")
command = args[0]
if command == "auth" and "--non-interactive" in args:
    print(json.dumps({"client_id": "client", "code_verifier": "verifier", "serial": "serial",
                      "url": "https://amazon.com/ap/signin?openid.oa2.code_challenge=challenge"}))
elif command == "auth" and "--logout" in args:
    os.remove(os.path.join(config, "current_user.json"))
elif command == "register":
    if (option("--code"), option("--client-id"), option("--code-verifier"), option("--serial")) == (
            "amazon-code", "client", "verifier", "serial"):
        save(os.path.join(config, "current_user.json"), {"name": "Tester", "user_id": "user"})
    print('{"tokens": "SECRET-TOKEN"}')
elif command == "library":
    save(os.path.join(config, "library.json"), [
        {"id": "amzn-2", "product": {"title": "Lumen", "asin": "B01"}},
        {"id": "amzn-1", "product": {"title": "Lumen", "asin": "B01"}},
        {"id": "amzn-3", "product": {"title": "Quiet Tide"}}])
elif command == "install":
    path = option("--path")
    os.makedirs(path, exist_ok=True)
    open(os.path.join(path, "Lumen.exe"), "w").close()
    progress()
    save(os.path.join(config, "installed.json"), [{"id": args[1], "path": path, "version": "1"}])
elif command == "launch":
    game = load(os.path.join(config, "installed.json"), [])[0]
    print(json.dumps({"command": {"instruction": os.path.join(game["path"], "Lumen.exe"), "arguments": ["-fuel"]},
                      "game_directory": game["path"], "env": {"FUEL_DIR": "/nile/SDK/Legacy"}}))
"""
)


def install_store_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put the stand-ins where Qtris finds its components, with their folders in tmp_path."""
    runtime = tmp_path / "runtime"
    for name, script in (("legendary", LEGENDARY), ("nile", NILE)):
        binary = runtime / name / name
        binary.parent.mkdir(parents=True)
        binary.write_text(f"#!{sys.executable}\n{script}", encoding="utf-8")
        binary.chmod(0o755)
    monkeypatch.setattr(settings, "RUNTIME_DIR", str(runtime))
    config = tmp_path / "config"
    monkeypatch.setattr(legendary, "CONFIG_PATH", str(config / "legendary"))
    monkeypatch.setattr(
        legendary, "CLIENT", StoreClient("legendary", "LEGENDARY_CONFIG_PATH", str(config / "legendary"))
    )
    monkeypatch.setattr(nile, "CONFIG_PATH", str(config / "nile"))
    monkeypatch.setattr(nile, "CLIENT", StoreClient("nile", "NILE_CONFIG_PATH", str(config)))
