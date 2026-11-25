"""Update the components Qtris runs games with: umu, gogdl, Winetricks and the
rest. Progress is announced with RUNTIME_UPDATE_CHANGED."""

from __future__ import annotations

import asyncio
import html
import re
import threading
from typing import TYPE_CHECKING

from qtris import settings
from qtris.backend.models import RuntimeComponentProgress, RuntimeUpdateStatus
from qtris.exceptions import QtrisError
from qtris.runtime import CHECK_INTERVAL as CHECK_INTERVAL
from qtris.runtime import RuntimeUpdater, get_components
from qtris.util.linux import LINUX_SYSTEM
from qtris.util.log import logger
from qtris.util.notifications import NotificationSource
from qtris.util.wine.wine import clear_wine_version_cache

if TYPE_CHECKING:
    from collections.abc import Sequence

    from qtris.runtime import ComponentUpdater

# Fired with a RuntimeUpdateStatus while an update runs, and once when it ends
RUNTIME_UPDATE_CHANGED = NotificationSource()

AUTO_UPDATE_SETTING = "auto_update_runtime"
POLL_SECONDS = 0.5

_running = False
# Set by cancel_runtime_update() to stop the update running, whose updaters these are
_cancelled = threading.Event()
_updaters: list[ComponentUpdater] = []


def _plain(markup: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", markup or "")).strip()


def _status(updaters: Sequence[ComponentUpdater], running: bool) -> RuntimeUpdateStatus:
    components = []
    for updater in updaters:
        progress = updater.get_progress()
        components.append(
            RuntimeComponentProgress(
                name=updater.name,
                status=_plain(progress.label_markup),
                fraction=progress.progress if progress.progress is None else max(0.0, min(1.0, progress.progress)),
                finished=progress.has_ended,
            )
        )
    return RuntimeUpdateStatus(running=running, components=tuple(components))


async def get_runtime_update_status() -> RuntimeUpdateStatus:
    return RuntimeUpdateStatus(running=_running)


async def is_auto_update_enabled() -> bool:
    return settings.read_bool_setting(AUTO_UPDATE_SETTING, default=True)


async def set_auto_update_enabled(enabled: bool) -> None:
    settings.write_setting(AUTO_UPDATE_SETTING, enabled)


async def list_installed_versions() -> tuple[tuple[str, str], ...]:
    def read() -> tuple[tuple[str, str], ...]:
        versions = [
            (component.name, version)
            for component in get_components(LINUX_SYSTEM.arch)
            if (version := component.installed_version()) is not None
        ]
        return tuple(sorted(versions, key=lambda item: item[0].casefold()))

    return await asyncio.to_thread(read)


async def update_runtime(force: bool = False) -> tuple[str, ...]:
    """Download the runtime components that are out of date, and return their names.
    Without force this only checks when the last check is stale and automatic updates
    are on, as at startup. Returns nothing when an update is already running."""
    global _running
    if _running:
        return ()
    _running = True
    _cancelled.clear()
    updaters: list[ComponentUpdater] = []
    try:

        def plan() -> tuple[RuntimeUpdater, list[ComponentUpdater]]:
            runtime_updater = RuntimeUpdater(force=force)
            return runtime_updater, runtime_updater.create_component_updaters()

        runtime_updater, updaters = await asyncio.to_thread(plan)
        _updaters[:] = updaters
        if _cancelled.is_set():
            return ()
        if not updaters:
            logger.debug("The runtime is up to date")
            return ()

        def install() -> None:
            failures = []
            for updater in updaters:
                if _cancelled.is_set():
                    break
                try:
                    updater.install_update(runtime_updater)
                    updater.join()
                except QtrisError as error:
                    failures.append(error.message)
            # Proton builds come from outside Qtris too
            clear_wine_version_cache()
            if failures:
                raise QtrisError("\n".join(failures))

        installing = asyncio.create_task(asyncio.to_thread(install))
        while not installing.done():
            RUNTIME_UPDATE_CHANGED.fire(_status(updaters, running=True))
            await asyncio.wait({installing}, timeout=POLL_SECONDS)
        await installing
        return tuple(updater.name for updater in updaters)
    finally:
        _running = False
        _updaters.clear()
        RUNTIME_UPDATE_CHANGED.fire(_status(updaters, running=False))


def cancel_runtime_update() -> None:
    """Stop the update running without waiting for it: its downloads stop, and the
    components not started yet are left for the next check. Its thread would otherwise
    keep Qtris running after it quits, until every download is done."""
    _cancelled.set()
    for updater in _updaters:
        updater.cancel()
