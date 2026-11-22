"""Application preferences: game sources, storage paths, the Steam account used for
shortcuts, and information about the system for bug reports."""

import asyncio
import json
import os
from collections.abc import Callable
from gettext import gettext as _
from pathlib import Path

from qtris import settings
from qtris.backend.models import SourceSetting, SteamAccount, StoragePaths, SystemInfoSection
from qtris.cache import get_custom_cache_path, save_custom_cache_path, validate_custom_cache_path
from qtris.config import QtrisConfig
from qtris.exceptions import MissingExecutableError, QtrisError
from qtris.services import SERVICES
from qtris.util import linux, system
from qtris.util.linux import gather_system_info, gather_system_info_dict
from qtris.util.log import get_log_contents
from qtris.util.steam.config import STEAM_ACCOUNT_SETTING, get_steam_users
from qtris.util.wine import proton


async def save_issue_report(filename: str, description: str) -> None:
    if not filename:
        raise QtrisError(_("Choose where to save the issue report."))

    def write() -> None:
        report = {"comment": description, "system": gather_system_info()}
        Path(filename).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    await asyncio.to_thread(write)


# Sources


async def list_sources() -> tuple[SourceSetting, ...]:
    """Return every game source, enabled or not, in alphabetical order."""
    sources = [
        SourceSetting(
            id=service_id,
            name=str(service.name),
            description=str(getattr(service, "description", "") or ""),
            enabled=settings.read_bool_setting(service_id, section="services"),
            in_games_view=settings.read_bool_setting(service_id + "_in_games_view", default=True, section="services"),
        )
        for service_id, service in SERVICES.items()
    ]
    return tuple(sorted(sources, key=lambda source: source.name.casefold()))


def _check_source(source_id: str) -> None:
    if source_id not in SERVICES:
        raise QtrisError(_("There is no game source called '%s'.") % source_id)


async def set_source_enabled(source_id: str, enabled: bool) -> None:
    """Turn a source on or off. Its games show in the Games view again either way, since
    a disabled source has no view of its own to show them in."""
    _check_source(source_id)
    settings.write_setting(source_id, enabled, section="services")
    settings.write_setting(source_id + "_in_games_view", True, section="services")


async def set_source_in_games_view(source_id: str, shown: bool) -> None:
    _check_source(source_id)
    settings.write_setting(source_id + "_in_games_view", shown, section="services")


# Storage


def _storage_paths() -> StoragePaths:
    system_config = QtrisConfig().system_config
    cache_path = get_custom_cache_path() or ""
    valid, message = validate_custom_cache_path(cache_path) if cache_path else (True, None)
    return StoragePaths(
        game_path=str(system_config.get("game_path") or os.path.expanduser("~/Games")),
        installer_cache_path=cache_path,
        installer_cache_message=message or "",
        installer_cache_valid=valid,
    )


def _save_system_option(key: str, value: str) -> None:
    config = QtrisConfig()
    if value:
        config.raw_system_config[key] = value
    else:
        config.raw_system_config.pop(key, None)
    config.save()


async def get_storage_paths() -> StoragePaths:
    return await asyncio.to_thread(_storage_paths)


async def set_game_path(path: str) -> StoragePaths:
    """Set the folder games install into by default."""
    await asyncio.to_thread(_save_system_option, "game_path", path.strip())
    return await get_storage_paths()


async def set_installer_cache_path(path: str) -> StoragePaths:
    """Keep installers' downloads in a folder, or discard them when given none."""
    save_custom_cache_path(path.strip())
    return await get_storage_paths()


# Steam account


async def list_steam_accounts() -> tuple[SteamAccount, ...]:
    """Return the Steam accounts on this computer, and which one Qtris uses."""
    active = settings.read_setting(STEAM_ACCOUNT_SETTING)
    users = await asyncio.to_thread(get_steam_users)
    return tuple(
        SteamAccount(
            id=str(user["steamid64"]),
            name=str(user.get("PersonaName") or f"#{user['steamid64']}"),
            active=str(user["steamid64"]) == active,
        )
        for user in users
    )


async def set_steam_account(account_id: str) -> None:
    """Choose the Steam account used for Steam integration and shortcuts."""
    settings.write_setting(STEAM_ACCOUNT_SETTING, account_id)


# System information


def _has_umu() -> bool:
    try:
        proton.get_umu_path()
    except MissingExecutableError:
        return False
    return True


_FEATURES: tuple[tuple[str, Callable[[], object]], ...] = (
    (_("Vulkan support"), linux.LINUX_SYSTEM.is_vulkan_supported),
    (_("umu installed"), _has_umu),
    (_("Gamescope"), lambda: system.can_find_executable("gamescope")),
    (_("Mangohud"), lambda: system.can_find_executable("mangohud")),
    (_("Steam"), linux.LINUX_SYSTEM.has_steam),
    (_("In Flatpak"), linux.LINUX_SYSTEM.is_flatpak),
)


def _system_info() -> tuple[SystemInfoSection, ...]:
    features = tuple((name, _("YES") if check() else _("NO")) for name, check in _FEATURES)
    sections = [SystemInfoSection(title=_("Features"), items=features)]
    for title, values in gather_system_info_dict().items():
        sections.append(SystemInfoSection(title=str(title), items=tuple((str(k), str(v)) for k, v in values.items())))
    return tuple(sections)


async def get_system_info() -> tuple[SystemInfoSection, ...]:
    return await asyncio.to_thread(_system_info)


def system_info_text(sections: tuple[SystemInfoSection, ...]) -> str:
    """Return system information as text to paste into a bug report."""
    lines = []
    for section in sections:
        lines.append(f"[{section.title}]")
        lines.extend(f"{name}: {value}" for name, value in section.items)
    return "\n".join(lines)


async def get_log() -> str:
    """Return this session's Qtris log, for bug reports."""
    return get_log_contents()
