"""Look up the umu IDs that select a store game's Proton fixes, the way Heroic does."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import requests

from qtris import settings
from qtris.util.http import get_request_proxies
from qtris.util.log import logger

UMU_API_URL = "https://umu.openwinecomponents.org/umu_api.php"
# Found IDs are reused for six hours. Misses and failed lookups are asked again next time
CACHE_SECONDS = 6 * 60 * 60
REQUEST_TIMEOUT = 3
# The umu database names a few stores differently from Qtris
STORE_NAMES = {"ea_app": "ea", "humblebundle": "humble"}
# umu's database doesn't track these
UNTRACKED_STORES = frozenset({"itchio", "steam", "flathub", "xdg"})

_lock = threading.Lock()


def _cache_path() -> Path:
    return Path(settings.CACHE_DIR) / "umu-ids.json"


def _read_cache() -> dict[str, dict[str, object]]:
    try:
        data: object = json.loads(_cache_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        logger.debug("Ignoring the umu ID cache: %s", error)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def _write_cache(cache: dict[str, dict[str, object]]) -> None:
    path = _cache_path()
    temporary = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(cache), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as error:
        logger.debug("Could not save the umu ID cache: %s", error)


def _cached_id(key: str) -> str | None:
    entry = _read_cache().get(key)
    if not entry:
        return None
    umu_id, checked = entry.get("umu_id"), entry.get("checked")
    if isinstance(umu_id, str) and umu_id and isinstance(checked, (int, float)):
        if 0 <= time.time() - checked < CACHE_SECONDS:
            return umu_id
    return None


def _remember(key: str, umu_id: str) -> None:
    now = time.time()
    cache = {
        name: entry
        for name, entry in _read_cache().items()
        if isinstance(checked := entry.get("checked"), (int, float)) and 0 <= now - checked < CACHE_SECONDS
    }
    cache[key] = {"umu_id": umu_id, "checked": now}
    _write_cache(cache)


def _fetch(store: str, codename: str) -> str | None:
    response = requests.get(
        UMU_API_URL,
        params={"store": store, "codename": codename},
        proxies=get_request_proxies(UMU_API_URL),
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    matches: object = response.json()
    if isinstance(matches, list):
        for match in matches:
            if isinstance(match, dict) and isinstance(umu_id := match.get("umu_id"), str) and umu_id:
                return umu_id
    return None


def get_umu_id(store: str, appid: str) -> str | None:
    """Return the umu ID of a store game, or None when umu has no fixes for it.

    This may wait on the network, so call it off the main loop.

    Parameters
    ----------
    store : str
        The Qtris service ID or umu store name of the game.
    appid : str
        The game's ID in that store.

    Returns
    -------
    str or None
        The umu ID, or None for unknown games and failed lookups.
    """
    store = STORE_NAMES.get(store.casefold(), store.casefold())
    codename = appid.casefold()
    if not store or not codename or store in UNTRACKED_STORES:
        return None
    key = f"{store}/{codename}"
    with _lock:
        if umu_id := _cached_id(key):
            return umu_id
    try:
        umu_id = _fetch(store, codename)
    except (requests.RequestException, ValueError) as error:
        logger.warning("Could not look up Proton fixes for %s: %s", key, error)
        return None
    if umu_id:
        with _lock:
            _remember(key, umu_id)
    return umu_id
