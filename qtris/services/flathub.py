"""Discover games and emulators in Flathub's AppStream catalog."""

from __future__ import annotations

import gzip
from gettext import gettext as _
from threading import Lock
from typing import TYPE_CHECKING
from xml.etree import ElementTree

import requests

from qtris import settings
from qtris.config import write_game_config
from qtris.database import games, sql
from qtris.database.services import ServiceGameCollection
from qtris.exceptions import QtrisError
from qtris.services.base import BaseService
from qtris.util import flatpak
from qtris.util.linux import LINUX_SYSTEM
from qtris.util.strings import slugify

if TYPE_CHECKING:
    from io import BufferedIOBase

    from qtris.database.services import DBServiceGame
    from qtris.delegates import Delegate

_CATALOG_URL = "https://dl.flathub.org/repo/appstream/{arch}/appstream.xml.gz"
_CATEGORIES = {"Game", "Emulator"}
_SCAN_LOCK = Lock()


def _parse_catalog(stream: BufferedIOBase) -> dict[str, str]:
    events = ElementTree.iterparse(stream, events=("start", "end"))
    _, root = next(events)
    if root.tag != "components":
        raise ValueError("Not an AppStream catalog")
    apps: dict[str, str] = {}
    for event, component in events:
        if event != "end" or component.tag != "component":
            continue
        if _CATEGORIES.intersection(category.text for category in component.findall("categories/category")):
            bundle = component.findtext("bundle[@type='flatpak']", default="")
            ref = bundle.split("/")
            name = component.findtext("name", default="").strip()
            if len(ref) == 4 and ref[0] == "app" and flatpak.is_app_id(ref[1]) and name:
                apps[ref[1]] = name
        root.clear()
    return apps


class FlathubService(BaseService):
    id = "flathub"
    name = _("Flathub")
    icon = "flathub"

    def reload(self) -> None:
        self.load()
        self.scan_installed_apps()

    def load(self) -> None:
        arch = LINUX_SYSTEM.arch
        if not arch:
            raise QtrisError(_("Flathub is unavailable on this architecture."))
        try:
            with requests.get(_CATALOG_URL.format(arch=arch), stream=True, timeout=(5, 30)) as response:
                response.raise_for_status()
                with gzip.GzipFile(fileobj=response.raw) as stream:
                    apps = _parse_catalog(stream)
        except (requests.RequestException, OSError, EOFError, ElementTree.ParseError, ValueError) as error:
            raise QtrisError(_("Could not load the Flathub catalog.")) from error
        if not apps:
            raise QtrisError(_("The Flathub catalog contained no games or emulators."))

        with sql.db_cursor(settings.DB_PATH) as cursor:
            cursor.execute("DELETE FROM service_games WHERE service = ?", (self.id,))
            cursor.executemany(
                "INSERT INTO service_games (service, appid, name, slug, icon) VALUES (?, ?, ?, ?, ?)",
                ((self.id, appid, name, slugify(name) or appid, appid) for appid, name in apps.items()),
            )

    def scan_installed_apps(self) -> bool:
        """Reconcile catalog entries with host apps, retaining library records and settings."""
        with _SCAN_LOCK:
            catalog = ServiceGameCollection.get_for_service(self.id)
            if not catalog:
                return False
            installed = flatpak.get_installed_apps()
            library = {row["service_id"]: row for row in games.get_games(filters={"service": self.id})}
            changed = False
            for appid, row in library.items():
                if appid not in installed and row["installed"] and row["runner"] == "flatpak":
                    sql.db_update(settings.DB_PATH, "games", {"installed": 0}, {"id": row["id"]})
                    changed = True
            for entry in catalog:
                appid = str(entry["appid"])
                if appid not in installed:
                    continue
                existing = library.get(appid)
                if existing and existing["runner"] not in (None, "", "flatpak"):
                    continue
                if existing and existing["installed"] and existing["configpath"] and existing.get("external_files"):
                    continue
                fields: dict[str, str | int] = {"installed": 1, "runner": "flatpak", "external_files": 1}
                if not existing or not existing["configpath"] or not existing["runner"]:
                    fields["configpath"] = write_game_config(slugify(appid), {"game": {"appid": appid}})
                if existing:
                    sql.db_update(settings.DB_PATH, "games", fields, {"id": existing["id"]})
                else:
                    games.add_game(name=entry["name"], slug=entry["slug"], service=self.id, service_id=appid, **fields)
                changed = True
            return changed

    def install(self, db_game: DBServiceGame, ui_delegate: Delegate, update: bool = False) -> None:
        flatpak.open_app_store(str(db_game["appid"]))

    def get_store_url(self, db_game: DBServiceGame) -> str:
        return f"https://flathub.org/apps/{db_game['appid']}"
