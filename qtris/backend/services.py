"""Calls about the games services (Steam, GOG, ...) offer."""

import asyncio
from dataclasses import dataclass
from gettext import gettext as _

from qtris import settings
from qtris.backend.models import ServiceGameInfo, ServiceInfo
from qtris.backend.store_login import login_input
from qtris.database import games as games_db
from qtris.database.services import ServiceGameCollection
from qtris.exceptions import QtrisError
from qtris.services import SERVICES, get_enabled_services
from qtris.services.base import AuthTokenExpiredError, BaseService, OnlineService
from qtris.services.flathub import FlathubService
from qtris.util.flatpak import FlatpakError
from qtris.util.log import logger


@dataclass(frozen=True)
class SourceStatus:
    name: str
    online: bool
    signed_in: bool
    can_sign_in: bool


@dataclass(frozen=True)
class SourceLauncher:
    id: str
    name: str
    directory: str


def _get_service(service_id: str) -> BaseService:
    service = SERVICES.get(service_id)
    if service is None:
        raise QtrisError(_("This game source is unavailable."))
    return service()


async def get_source_status(service_id: str) -> SourceStatus:
    return await asyncio.to_thread(_get_source_status, service_id)


async def list_source_launchers(service_id: str) -> tuple[SourceLauncher, ...]:
    launchers = await asyncio.to_thread(_get_service(service_id).list_launchers)
    return tuple(SourceLauncher(item["id"], item["name"], item.get("directory") or "") for item in launchers)


async def set_source_launcher(service_id: str, game_id: str) -> None:
    if not any(launcher.id == game_id for launcher in await list_source_launchers(service_id)):
        raise QtrisError(_("This launcher is no longer available."))
    settings.write_setting(f"{service_id}_launcher", game_id, section="services")


def _get_source_status(service_id: str) -> SourceStatus:
    service = _get_service(service_id)
    online = isinstance(service, OnlineService)
    signed_in = isinstance(service, OnlineService) and bool(service.is_authenticated())
    return SourceStatus(str(service.name), online, signed_in, login_input(service_id) is not None)


async def sync_source(service_id: str) -> None:
    await asyncio.to_thread(_sync_source, service_id)


async def refresh_flathub_installations() -> bool:
    """Update host-managed entries without fetching the catalog again."""
    if FlathubService.id not in get_enabled_services():
        return False
    try:
        return await asyncio.to_thread(FlathubService().scan_installed_apps)
    except FlatpakError as error:
        logger.warning("Could not refresh Flathub installations: %s", error)
        return False


def _sync_source(service_id: str) -> None:
    service = _get_service(service_id)
    if isinstance(service, OnlineService) and not service.is_authenticated():
        raise QtrisError(_("Sign in to sync %s.") % service.name)
    try:
        service.reload()
    except AuthTokenExpiredError:
        if isinstance(service, OnlineService):
            service.logout()
        raise QtrisError(_("Your session expired. Sign in again to sync %s.") % service.name) from None


async def disconnect_source(service_id: str) -> None:
    await asyncio.to_thread(_disconnect_source, service_id)


def _disconnect_source(service_id: str) -> None:
    service = _get_service(service_id)
    if isinstance(service, OnlineService):
        service.logout()


async def list_services() -> list[ServiceInfo]:
    """Return the services the user has enabled, in alphabetical order of their names."""
    services = [
        ServiceInfo(service_id, str(service_class.name), bool(service_class.online))
        for service_id, service_class in get_enabled_services().items()
    ]
    return sorted(services, key=lambda s: s.name.casefold())


async def list_service_games(service: str | None = None, installed_only: bool = False) -> list[ServiceGameInfo]:
    """Return the games one service offers, or all services if service is None, with
    whether each is installed in the library."""
    installed = {
        (game["service"], game.get("service_id"))
        for game in games_db.get_games(filters={"installed": 1})
        if not service or game["service"] == service
    }
    if service:
        db_games = ServiceGameCollection.get_for_service(service)
    else:
        db_games = ServiceGameCollection.get_service_games()
    games = [
        ServiceGameInfo(
            id=str(db_game["id"]),
            name=str(db_game["name"]),
            slug=str(db_game["slug"]),
            service=str(db_game["service"]),
            appid=str(db_game["appid"]),
            installed=(db_game["service"], db_game["appid"]) in installed,
            details=str(db_game["details"]) if db_game.get("details") else None,
        )
        for db_game in db_games
    ]
    return [game for game in games if game.installed] if installed_only else games
