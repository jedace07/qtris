"""Expose runner management to Qt Quick."""

from __future__ import annotations

from gettext import gettext as _
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtQml import QmlElement

from qtris.backend import runners
from qtris.qt.qml_types import Property, QmlUncreatable
from qtris.qt.tasks import start_task

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable, Coroutine
    from typing import Any

    from qtris.qt.delegates import PromptController

QML_IMPORT_NAME = "QtrisBridge"
QML_IMPORT_MAJOR_VERSION = 1


@QmlElement
@QmlUncreatable("Qtris creates this type in Python")
class RunnerManagerController(QObject):
    stateChanged = Signal()
    progressChanged = Signal()

    def __init__(self, prompts: PromptController, parent: QObject) -> None:
        super().__init__(parent)
        self._prompts = prompts
        self._platforms: tuple[runners.LocalGamePlatform, ...] = ()
        self._builds: tuple[runners.ProtonBuild, ...] = ()
        self._family = 0
        self._releases: dict[str, tuple[str, ...]] = {}
        self._latest: runners.LatestProton | None = None
        self._proton = False
        self._task: asyncio.Task[None] | None = None
        self._message = ""
        self._fraction = -1.0

    @Property("QVariantList", notify=stateChanged)
    def entries(self) -> list[dict[str, str | bool]]:
        return self._entries()

    def _entries(self) -> list[dict[str, str | bool]]:
        if self._proton:
            latest = [self._latest_entry(self._latest)] if self._latest else []
            return [
                *latest,
                *(
                    {
                        "key": build.version,
                        "label": build.label or build.version,
                        "description": "",
                        "installed": build.installed,
                        "canInstall": not build.installed,
                        "canUpdate": False,
                        "canRemove": build.can_remove,
                    }
                    for build in self._builds
                ),
            ]
        return [
            {
                "key": entry.name,
                "label": entry.label,
                "description": entry.description,
                "manageProton": entry.name == "wine",
            }
            for entry in self._platforms
        ]

    def _latest_entry(self, latest: runners.LatestProton) -> dict[str, str | bool]:
        installed = bool(latest.release)
        newest = next(iter(self._releases.get(self._family_name, ())), "")
        can_update = installed and bool(newest) and newest != latest.release
        if can_update:
            description = _("Currently %s. A newer release is out.") % latest.release_label
        elif installed:
            description = _("Currently %s. Qtris updates it with its other components.") % latest.release_label
        else:
            description = _("Follows the newest release. Install it here, or Qtris does when a game needs it.")
        return {
            "key": latest.version,
            "label": latest.label,
            "description": description,
            "installed": installed,
            "canInstall": not installed,
            "canUpdate": can_update,
            "canRemove": installed,
        }

    @property
    def _family_name(self) -> str:
        return runners.PROTON_FAMILIES[self._family]

    @Property(bool, notify=stateChanged)
    def proton(self) -> bool:
        return self._proton

    @Property("QVariantList", constant=True)
    def protonFamilies(self) -> list[str]:  # noqa: N802
        return list(runners.PROTON_FAMILIES)

    @Property(int, notify=stateChanged)
    def protonFamily(self) -> int:  # noqa: N802
        return self._family

    @Property(str, notify=stateChanged)
    def protonNote(self) -> str:  # noqa: N802
        if self._family_name == runners.PROTON_FAMILIES[0]:
            return _(
                "GE-Proton Latest follows the newest GE-Proton release, and is the default; Qtris updates it "
                "with its other components. Install a specific release to pin games to it."
            )
        return _(
            "Proton-CachyOS is tuned for performance, and Qtris installs the build optimized for x86-64-v3 "
            "processors when yours supports it. Proton-CachyOS Latest follows the newest release; Qtris "
            "updates it with its other components."
        )

    @Property(bool, notify=stateChanged)
    def busy(self) -> bool:
        return self._task is not None

    @Property(str, notify=progressChanged)
    def message(self) -> str:
        return self._message

    @Property(float, notify=progressChanged)
    def fraction(self) -> float:
        return self._fraction

    @Slot()
    def open(self) -> None:
        if self._task is None:
            self._proton = False
            self._start(self._load_platforms(), "")

    @Slot()
    def show_proton(self) -> None:
        if self._task is None:
            self._proton = True
            self._start(self._load_proton(), _("Looking up %s releases…") % self._family_name)

    @Slot(int)
    def select_family(self, index: int) -> None:
        if self._task is None and 0 <= index < len(runners.PROTON_FAMILIES) and index != self._family:
            self._family = index
            self.show_proton()

    @Slot(str)
    def install(self, key: str) -> None:
        entry = next((item for item in self._entries() if item["key"] == key and item.get("canInstall")), None)
        if self._task is None and entry:
            self._start(self._install(key), _("Installing %s…") % entry["label"])

    @Slot(str)
    def update(self, key: str) -> None:
        entry = next((item for item in self._entries() if item["key"] == key and item.get("canUpdate")), None)
        if self._task is None and entry:
            self._start(self._install(key), _("Updating %s…") % entry["label"])

    @Slot(str)
    def remove(self, key: str) -> None:
        entry = next((item for item in self._entries() if item["key"] == key and item.get("canRemove")), None)
        if self._task is None and entry:
            self._start(self._remove(key, str(entry["label"])), _("Removing %s…") % entry["label"])

    def _start(self, operation: Coroutine[Any, Any, None], message: str) -> None:
        self._task = start_task(self._run(operation), self._prompts.error)
        self._set_progress(message)
        self.stateChanged.emit()

    async def _run(self, operation: Coroutine[Any, Any, None]) -> None:
        try:
            await operation
        finally:
            self._task = None
            self._set_progress("")
            self.stateChanged.emit()

    def _set_progress(self, message: str, fraction: float | None = None) -> None:
        self._message = message
        self._fraction = fraction if fraction is not None else -1.0
        self.progressChanged.emit()

    async def _load_platforms(self) -> None:
        self._platforms = await runners.list_local_game_platforms()

    async def _load_proton(self) -> None:
        family = self._family_name
        self._latest = await runners.get_latest_proton(family)
        self._builds = await runners.list_proton_builds(family, self._releases.get(family, ()))
        self.stateChanged.emit()
        self._releases[family] = await runners.list_proton_releases(family)
        self._builds = await runners.list_proton_builds(family, self._releases[family])

    async def _install(self, key: str) -> None:
        interaction = runners.RunnerInstallInteraction(self._set_progress, self._ask, self._choose_file)
        try:
            await runners.install_runner("wine", key, interaction=interaction)
        finally:
            await self._refresh_builds()

    async def _remove(self, key: str, label: str) -> None:
        if not await self._ask(_("Remove Proton build"), _("Move %s to the Trash?") % label):
            return
        try:
            await runners.uninstall_runner("wine", key)
        finally:
            await self._refresh_builds()

    async def _refresh_builds(self) -> None:
        family = self._family_name
        self._latest = await runners.get_latest_proton(family)
        self._builds = await runners.list_proton_builds(family, self._releases.get(family, ()))

    async def _ask(self, title: str, message: str) -> bool:
        return await self._prompts.ask_async("question", title, message) is not None

    async def _choose_file(self, title: str, question: str, message: str) -> str | None:
        if not await self._ask(title, question):
            return None
        return await self._prompts.ask_async("file", message, "")

    async def prepare_for_launch(self, game_id: str, progress: Callable[[str, float | None], None]) -> bool:
        interaction = runners.RunnerInstallInteraction(progress, self._ask, self._choose_file)
        return await runners.prepare_game_runner(game_id, interaction)
