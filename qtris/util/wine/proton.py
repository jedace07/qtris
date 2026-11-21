"""Utility module to deal with Proton and umu"""

import csv
import os
import re
from collections.abc import Generator
from gettext import gettext as _
from typing import TYPE_CHECKING

from qtris import settings
from qtris.exceptions import MissingExecutableError
from qtris.monitored_command import RUNNING_COMMANDS
from qtris.util import cache_single, system
from qtris.util.linux import LINUX_SYSTEM
from qtris.util.steam.config import get_steamapps_dirs
from qtris.util.strings import get_natural_sort_key
from qtris.util.wine import proton_releases

if TYPE_CHECKING:
    from qtris.game import Game

DEFAULT_GAMEID = "umu-default"
# umu's name for the newest GE-Proton release, which it downloads by itself when told
# to run without a Proton build
UMU_GE_LATEST = "GE-Latest"


# Matches a umu log line such as
#   [umu.umu_proton:348] INFO: Downloading GE-Proton10-34.tar.gz...
_UMU_LOG_LINE_RE = re.compile(r"\[umu\.[\w_]+:\d+\]\s+(?:INFO|WARNING|ERROR):\s+(.+)")


class UmuLaunchStatusParser:
    """Scans umu's stdout for runtime setup progress and pushes it onto a
    Game's launch_status. The game appears frozen on 'Launching' while umu
    fetches GE-Proton / the sniper runtime; this surfaces the current step
    to the UI. MonitoredCommand delivers raw read chunks rather than whole
    lines, so the parser buffers partial lines across calls."""

    def __init__(self, game: "Game"):
        self.game = game
        self._buffer = ""

    def __call__(self, line: str) -> None:
        # MonitoredCommand delivers raw read chunks, not individual lines,
        # so this may contain zero, one, or many newlines.
        data = self._buffer + line
        lines = data.split("\n")
        self._buffer = lines[-1]
        for complete_line in lines[:-1]:
            self._process_line(complete_line)

    def _process_line(self, line: str) -> None:
        match = _UMU_LOG_LINE_RE.search(line)
        if not match:
            return
        message = match.group(1).strip()
        # "Using <Proton>" is logged at the end of umu's setup phase, right
        # before it hands off to Proton — clear any pending status then.
        if message.startswith("Using "):
            self.game.launch_status = ""
            return
        # Only the user-meaningful progress messages — skip lock acquisition,
        # version chatter, env dumps, etc.
        if message.startswith(("Downloading ", "Extracting ", "Checking updates")):
            self.game.launch_status = message


def is_proton_version(version: str | None) -> bool:
    """True if the version indicated specifies a Proton version of Wine; these
    require special handling. This does not recognize the special 'ge-proton' version
    found in configurations; this must be specially handled elsewhere."""
    return find_proton_version(version) is not None


def find_proton_version(version: str | None) -> str | None:
    """Return the name under which a Proton version is installed, or None if it isn't.

    GE-Proton and Proton-CachyOS releases are stored by release name, but their builds
    install into folders named after the architecture too, so the version matches
    either name."""
    if not version:
        return None
    proton_versions = get_proton_versions()
    candidates = (
        proton_releases.get_build_folder_names(version)
        if proton_releases.is_proton_release(version)
        else (version, "%s-%s" % (version, LINUX_SYSTEM.arch))
    )
    for candidate in candidates:
        if candidate in proton_versions:
            return candidate
    return None


def is_umu_path(path: str | None) -> bool:
    """True if the path given actually runs Umu; this will run Proton-Wine in turn,
    but can be directed to particular Proton implementation by setting the env-var
    PROTONPATH, but if this is omitted it will default to the latest Proton it
    downloads."""
    return bool(path and (path.endswith("/umu_run.py") or path.endswith("/umu-run")))


def is_proton_path(wine_path: str | None) -> bool:
    """True if the path given actually runs Umu; this will run Proton-Wine in turn,
    but can be directed to particular Proton implementation by setting the env-var
    PROTONPATH, but if this is omitted it will default to the latest Proton it
    downloads.

    This function may be given the wine root directory or a file within such as
    the wine executable and will return true for either."""
    if not wine_path:
        return True
    for candidate_wine_path in get_proton_versions().values():
        if system.path_contains(candidate_wine_path, wine_path):
            return True
    return False


def is_wine_only_prefix(prefix_path: str | None) -> bool:
    """True if prefix_path holds a Wine prefix that Proton has never run in; umu and
    Proton leave 'tracked_files' and 'version' files in the prefixes they manage."""
    if not prefix_path or not system.path_exists(os.path.join(prefix_path, "user.reg")):
        return False
    return not any(system.path_exists(os.path.join(prefix_path, name)) for name in ("tracked_files", "version"))


@cache_single
def get_umu_path() -> str:
    """Returns the path to the Umu launch script, which can be run to execute
    a Proton version. It can supply a default Proton, but if the env-var PROTONPATH
    is set this will direct it to a specific Proton installation.

    The path that this returns will be considered an Umu path by is_umu_path().

    If this script can't be found this will raise MissingExecutableError."""

    # 'umu-run' is normally the entry point, and is a zipapp full of Python code. But
    # We used to ship a directory of loose files, and the entry point then is 'umu_run.py'
    entry_points = ["umu-run", "umu_run.py"]

    custom_path = settings.read_setting("umu_path")
    if custom_path:
        for entry_point in entry_points:
            entry_path = os.path.join(custom_path, entry_point)
            if system.path_exists(entry_path):
                return entry_path

    # We only use 'umu-run' when searching the path since that's the command
    # line entry point.
    if system.can_find_executable("umu-run"):
        return system.find_required_executable("umu-run")
    path_candidates = (
        "/app/share",  # prioritize flatpak due to non-rolling release distros
        "/usr/local/share",
        "/usr/share",
        "/opt",
        settings.RUNTIME_DIR,
    )
    for path_candidate in path_candidates:
        for entry_point in entry_points:
            entry_path = os.path.join(path_candidate, "umu", entry_point)
            if system.path_exists(entry_path):
                return entry_path
    raise MissingExecutableError("Install umu to use Proton")


def get_proton_wine_files_dir(version: str) -> str | None:
    """Get the directory holding the Wine files of the specified Proton version; this is
    the directory containing 'bin', which older builds call 'dist' and newer ones 'files'.
    Returns None if the version isn't installed or ships no wine executable."""
    installed_version = find_proton_version(version)
    proton_path = get_proton_versions()[installed_version] if installed_version else None
    if proton_path:
        for files_dir in ("dist", "files"):
            files_path = os.path.join(proton_path, files_dir)
            if os.path.exists(os.path.join(files_path, "bin/wine")):
                return files_path

    return None


def get_proton_wine_path(version: str) -> str:
    """Get the wine path for the specified proton version"""
    files_path = get_proton_wine_files_dir(version)
    if files_path:
        return os.path.join(files_path, "bin/wine")

    raise MissingExecutableError(_("Proton version '%s' is missing its wine executable and can't be used.") % version)


def get_proton_path_by_path(wine_path: str) -> str:
    # Split the path to get the directory containing the file
    directory_path = os.path.dirname(wine_path)

    # Navigate up two levels to reach the version directory
    version_directory = os.path.dirname(os.path.dirname(directory_path))

    return version_directory


def list_proton_versions() -> list[str]:
    """Return the installed Proton builds, other than the rolling ones, in presentation
    order: GE-Proton, then Proton-CachyOS releases by release name, newest first, then
    Valve's Proton (Experimental, Hotfix, then by version, newest first), then any other
    builds by name."""
    releases: set[str] = set()
    valve: list[str] = []
    others: list[str] = []
    for name in get_proton_versions():
        release = proton_releases.get_release_of_build(name)
        if proton_releases.get_rolling(name):
            continue  # The rolling builds are offered on their own, ahead of the rest
        if release:
            releases.add(release)
        elif name.startswith("Proton "):
            valve.append(name)
        else:
            others.append(name)
    return [
        *proton_releases.sort_releases(releases),
        *_sort_valve_proton_versions(valve),
        *sorted(others, key=get_natural_sort_key),
    ]


def _sort_valve_proton_versions(names: list[str]) -> list[str]:
    """Sort Valve's Proton with Experimental first and Hotfix second, then numbered
    releases newest first."""

    def rank(name: str) -> int:
        return 0 if "Experimental" in name else 1 if "Hotfix" in name else 2

    numbered = sorted((name for name in names if rank(name) == 2), key=get_natural_sort_key, reverse=True)
    return [*sorted((name for name in names if rank(name) < 2), key=rank), *numbered]


@cache_single
def get_proton_versions() -> dict[str, str]:
    """Return the dict of Proton versions installed in Steam, which is cached.
    The keys are the versions, and the values are the paths to those versions,
    which are their wine-paths."""
    try:
        # We can only use a Proton install via the Umu launcher script.
        _ = get_umu_path()
    except MissingExecutableError:
        return {}

    versions = dict()
    # Rolling builds go by their configured version, not their folder's name
    for rolling in proton_releases.ROLLING:
        rolling_path = get_rolling_path(rolling)
        if os.path.isfile(os.path.join(rolling_path, "proton")):
            versions[rolling.version] = rolling_path
    rolling_paths = {get_rolling_path(rolling) for rolling in proton_releases.ROLLING}
    for proton_path in _iter_proton_locations():
        if os.path.isdir(proton_path):
            for version in os.listdir(proton_path):
                if version not in versions and os.path.join(proton_path, version) not in rolling_paths:
                    wine_path = os.path.join(proton_path, version)
                    if os.path.isfile(os.path.join(wine_path, "proton")):
                        versions[version] = wine_path
    return versions


def _iter_proton_locations() -> Generator[str, None, None]:
    """Iterate through all potential Proton locations"""
    yield get_proton_dir()
    yield settings.WINE_DIR
    yield get_steam_compat_dir()

    try:
        steamapp_dirs = get_steamapps_dirs()
    except Exception:
        return  # in case of corrupt or unreadable Steam configuration files!

    for path in [os.path.join(p, "common") for p in steamapp_dirs]:
        yield path

    for path in [os.path.join(p, "") for p in steamapp_dirs]:
        yield path


def _get_data_home() -> str:
    """Return the data folder Steam uses: the host's when running as a Flatpak."""
    data_home_var = "HOST_XDG_DATA_HOME" if LINUX_SYSTEM.is_flatpak() else "XDG_DATA_HOME"
    return os.environ.get(data_home_var) or os.path.expanduser("~/.local/share")


def get_proton_dir() -> str:
    """Return the folder where Qtris installs the Proton builds it downloads."""
    return settings.PROTON_DIR


def get_rolling_path(rolling: "proton_releases.RollingProton") -> str:
    """Return the folder Qtris keeps a rolling Proton build in."""
    return os.path.join(get_proton_dir(), rolling.folder)


def get_default_proton_path() -> str:
    """Return the PROTONPATH umu runs with when no Proton build is named: GE-Proton
    Latest, or umu's own copy of it if Qtris hasn't installed it yet."""
    ge_proton_latest = proton_releases.get_rolling(proton_releases.GE_PROTON_LATEST)
    if ge_proton_latest:
        path = get_rolling_path(ge_proton_latest)
        if os.path.isfile(os.path.join(path, "proton")):
            return path
    return UMU_GE_LATEST


def get_steam_compat_dir() -> str:
    """Return the compatibilitytools.d folder where Steam and umu keep Proton builds.
    Qtris used to install the releases it downloads here, and still lists and removes
    them. This is the host's data folder when running as a Flatpak."""
    return os.path.join(_get_data_home(), "Steam", "compatibilitytools.d")


def get_umu_folders_path() -> str:
    """Return where umu keeps its folders: the Steam Linux Runtime, its locks and any
    Proton it downloads by itself.

    umu would otherwise use XDG_DATA_HOME, or HOST_XDG_DATA_HOME under Flatpak, which
    puts them in the host's data folder, outside Qtris's sandbox. Players also
    override XDG_DATA_HOME or HOME per game to keep save files out of their home
    folder, which would send umu's downloads there too, for every game.
    UMU_FOLDERS_PATH keeps them in Qtris's own folder without touching what the game
    itself sees.
    """
    return settings.UMU_DATA_DIR


def update_proton_env(wine_path: str, env: dict[str, str], game_id: str = DEFAULT_GAMEID, umu_log: str = "") -> None:
    """Add various env-vars to an 'env' dict for use by Proton and Umu; this won't replace env-vars, so they can still
    be pre-set before we get here. This sets the PROTONPATH so the Umu launcher will know what Proton to use,
    and the WINEARCH to win64, which is what we expect Proton to always be. GAMEID is required, but we'll use a default
    GAMEID if you don't pass one in.

    This also propagates LC_ALL to HOST_LC_ALL, if LC_ALL is set."""

    if "PROTONPATH" not in env:
        if is_umu_path(wine_path):
            env["PROTONPATH"] = get_default_proton_path()
        else:
            env["PROTONPATH"] = get_proton_path_by_path(wine_path)

    if "GAMEID" not in env:
        env["GAMEID"] = game_id

    if "UMU_LOG" not in env and umu_log:
        env["UMU_LOG"] = umu_log

    if "WINEARCH" not in env:
        env["WINEARCH"] = "win64"

    if "PROTON_VERB" not in env:
        # Proton fixes are only applied with waitforexitandrun, so we want to use that
        # but only if we're the first process start - the next concurrent process should
        # use run so it does not wait.
        prefix = env.get("WINEPREFIX")
        if prefix and prefix in (c.env.get("WINEPREFIX") for c in RUNNING_COMMANDS):
            env["PROTON_VERB"] = "runinprefix"  # do *not* re-initialize a running prefix!
        else:
            env["PROTON_VERB"] = "waitforexitandrun"  # does full initialization with proton-fixes

    if "UMU_FOLDERS_PATH" not in env:
        env["UMU_FOLDERS_PATH"] = get_umu_folders_path()

    locale = env.get("LC_ALL")
    host_locale = env.get("HOST_LC_ALL")
    if locale and not host_locale:
        env["HOST_LC_ALL"] = locale


def get_game_id(game, env) -> str:
    if not game:
        return DEFAULT_GAMEID

    game_id = env.get("UMU_ID")
    if game_id:
        return game_id

    games_path = os.path.join(settings.RUNTIME_DIR, "umu-games", "umu-database.csv")

    if not os.path.exists(games_path):
        return DEFAULT_GAMEID

    store = "humble" if game.service == "humblebundle" else game.service
    with open(games_path, encoding="utf-8", newline="") as games_file:
        for row in csv.DictReader(games_file):
            if store and row["STORE"] == store and row["CODENAME"] == game.appid:
                return row["UMU_ID"]
    return DEFAULT_GAMEID


def get_anti_cheat_runtimes() -> dict[str, str]:
    """Locate the official anti-cheat runtimes installed by Steam."""
    names = {
        "PROTON_BATTLEYE_RUNTIME": "Proton BattlEye Runtime",
        "PROTON_EAC_RUNTIME": "Proton EasyAntiCheat Runtime",
    }
    paths = {}
    for steamapps in get_steamapps_dirs():
        for variable, name in names.items():
            path = os.path.join(steamapps, "common", name)
            if variable not in paths and os.path.isdir(path):
                paths[variable] = path
    return paths
