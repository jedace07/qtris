"""Drive game imports from Qt Quick."""

from __future__ import annotations

from collections.abc import Coroutine
from gettext import ngettext
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtQml import QmlElement

from qtris.backend import imports
from qtris.qt.delegates import PromptController
from qtris.qt.qml_types import Property, QmlUncreatable
from qtris.qt.tasks import start_task

QML_IMPORT_NAME = "QtrisBridge"
QML_IMPORT_MAJOR_VERSION = 1


@QmlElement
@QmlUncreatable("Qtris creates this type in Python")
class ImportController(QObject):
    stateChanged = Signal()

    def __init__(self, prompts: PromptController, parent: QObject) -> None:
        super().__init__(parent)
        self._prompts = prompts
        self._busy = False
        self._results: list[str] = []

    @Property(bool, notify=stateChanged)
    def busy(self) -> bool:
        return self._busy

    @Property("QVariantList", notify=stateChanged)
    def results(self) -> list[str]:
        return self._results[:]

    @Slot()
    def import_playtron(self) -> None:
        if not self._busy:
            self._start(self._import_playtron())

    def _start(self, operation: Coroutine[Any, Any, None]) -> None:
        self._busy = True
        self._results.clear()
        self.stateChanged.emit()
        start_task(self._run(operation), self._prompts.error)

    async def _run(self, operation: Coroutine[Any, Any, None]) -> None:
        try:
            await operation
        finally:
            self._busy = False
            self.stateChanged.emit()

    async def _import_playtron(self) -> None:
        count = await imports.import_playtron_games()
        self._results.append(ngettext("Imported %d game.", "Imported %d games.", count) % count)
