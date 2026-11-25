"""Update the components games run with (umu, gogdl, Winetricks...) for Qt Quick, and show how far it's got."""

from __future__ import annotations

import asyncio
from gettext import gettext as _

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtQml import QmlElement

from qtris.backend import runtime_updates
from qtris.backend.models import RuntimeUpdateStatus
from qtris.qt.delegates import PromptController
from qtris.qt.qml_types import Property, QmlUncreatable
from qtris.qt.tasks import start_task
from qtris.util.log import logger
from qtris.util.notifications import NotificationRegistration

QML_IMPORT_NAME = "QtrisBridge"
QML_IMPORT_MAJOR_VERSION = 1


@QmlElement
@QmlUncreatable("Qtris creates this type in Python")
class RuntimeUpdatesController(QObject):
    stateChanged = Signal()
    versionsChanged = Signal()

    def __init__(self, prompts: PromptController, parent: QObject) -> None:
        super().__init__(parent)
        self._prompts = prompts
        self._status = RuntimeUpdateStatus(running=False)
        self._result = ""
        self._auto_update = True
        self._task: asyncio.Task[None] | None = None
        self._versions: tuple[tuple[str, str], ...] = ()
        self._timer = QTimer(self)
        self._timer.setInterval(runtime_updates.CHECK_INTERVAL * 1000)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.update)
        self._registration: NotificationRegistration | None = runtime_updates.RUNTIME_UPDATE_CHANGED.register(
            self._on_changed
        )
        start_task(self._load(), self._prompts.error)

    async def _load(self) -> None:
        self._auto_update = await runtime_updates.is_auto_update_enabled()
        if self._auto_update:
            self._timer.start()
        self.stateChanged.emit()
        await self._load_versions()

    @Property(bool, notify=stateChanged)
    def running(self) -> bool:
        return self._status.running or (self._task is not None and not self._task.done())

    @Property(str, notify=stateChanged)
    def statusText(self) -> str:  # noqa: N802
        """What's being updated, or how the last update ended."""
        if not self._status.running:
            return self._result or (_("Checking for updates…") if self.running else "")
        active = [component for component in self._status.components if not component.finished]
        if not active:
            return _("Updating tools…")
        component = active[0]
        if component.fraction is None:
            return component.status
        return _("%(status)s (%(percent)d%%)") % {"status": component.status, "percent": component.fraction * 100}

    @Property(bool, notify=stateChanged)
    def autoUpdate(self) -> bool:  # noqa: N802
        return self._auto_update

    @Property("QVariantList", notify=versionsChanged)
    def versions(self) -> list[dict[str, str]]:
        return [{"name": name, "version": version} for name, version in self._versions]

    @Slot()
    def refresh_versions(self) -> None:
        start_task(self._load_versions(), self._prompts.error)

    async def _load_versions(self) -> None:
        versions = await runtime_updates.list_installed_versions()
        if versions != self._versions:
            self._versions = versions
            self.versionsChanged.emit()

    @Slot(bool)
    def set_auto_update(self, enabled: bool) -> None:
        if enabled == self._auto_update:
            return
        self._auto_update = enabled
        self.stateChanged.emit()
        start_task(self._save_auto_update(enabled), self._prompts.error)

    async def _save_auto_update(self, enabled: bool) -> None:
        await runtime_updates.set_auto_update_enabled(enabled)
        if enabled:
            self._timer.start()
            self.update()
        else:
            self._timer.stop()

    @Slot()
    def check(self) -> None:
        """Check for updates now, whatever the last check found."""
        self.update(force=True)

    def update(self, force: bool = False) -> None:
        if self._task is None or self._task.done():
            self._result = ""
            self._task = start_task(self._update(force), self._prompts.error)
            self._task.add_done_callback(self._update_finished)
            self.stateChanged.emit()

    def _update_finished(self, _task: asyncio.Task[None]) -> None:
        if self._auto_update and self._registration:
            self._timer.start()
        self.stateChanged.emit()

    async def wait_for_update(self) -> None:
        if self._task and not self._task.done():
            await asyncio.wait({self._task})

    async def _update(self, force: bool) -> None:
        try:
            updated = await runtime_updates.update_runtime(force=force)
        except Exception as error:
            self._result = _("Could not update the tools. Try checking again.")
            if force:
                raise
            logger.warning("Automatic tool update failed: %s", error, exc_info=error)
        else:
            if updated:
                self._result = _("Updated %s.") % ", ".join(updated)
            elif force:
                self._result = _("No updates were installed.")
        finally:
            await self._load_versions()

    def _on_changed(self, status: RuntimeUpdateStatus) -> None:
        self._status = status
        self.stateChanged.emit()

    def shutdown(self) -> None:
        self._timer.stop()
        if self._registration:
            self._registration.unregister()
            self._registration = None
        # The event loop stops with Qtris, so stop the update's thread directly
        runtime_updates.cancel_runtime_update()
