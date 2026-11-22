"""Expose store installers and local Windows setup to Qt Quick."""

from __future__ import annotations

import asyncio
from functools import partial
from gettext import gettext as _
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from qtris.backend import installers, local_games
from qtris.backend.errors import QtrisError
from qtris.backend.installers import InstallationKind
from qtris.qt.installation import InstallationController
from qtris.qt.tasks import start_task

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import Any

    from qtris.backend.models import InstallerCatalog, InstallerSelection
    from qtris.qt.delegates import PromptController


class InstallerController(QObject):
    requested = Signal()
    stateChanged = Signal()
    catalogChanged = Signal()
    closeRequested = Signal()
    existingStateChanged = Signal()
    existingGameImported = Signal(str)

    def __init__(self, prompts: PromptController, parent: QObject) -> None:
        super().__init__(parent)
        self._prompts = prompts
        self._game_name = ""
        self._catalog: InstallerCatalog | None = None
        self._selection: InstallerSelection | None = None
        self._choice_index = -1
        self._directory = ""
        self._error = ""
        self._kind = InstallationKind.INSTALL
        self._task: asyncio.Task[None] | None = None
        self._gog_target: tuple[str, str] | None = None
        self._importing = False
        self._import_error = ""
        self.installation_controller = InstallationController(prompts, self)

    @Property(QObject, constant=True)
    def installation(self) -> InstallationController:
        return self.installation_controller

    @Property(bool, notify=stateChanged)
    def loading(self) -> bool:
        return self._task is not None

    @Property(str, notify=stateChanged)
    def gameName(self) -> str:  # noqa: N802
        return self._catalog.game_name if self._catalog else self._game_name

    @Property(list, notify=catalogChanged)
    def choices(self) -> list[dict[str, str]]:
        if not self._catalog:
            return []
        return [
            {
                "version": choice.version,
                "description": choice.description,
                "notes": choice.notes,
            }
            for choice in self._catalog.choices
        ]

    @Property(bool, notify=stateChanged)
    def selected(self) -> bool:
        return self._selection is not None

    @Property(int, notify=stateChanged)
    def choiceIndex(self) -> int:  # noqa: N802
        return self._choice_index

    @Property(str, notify=stateChanged)
    def error(self) -> str:
        return self._error

    @Property(bool, notify=existingStateChanged)
    def canImportExisting(self) -> bool:  # noqa: N802
        return self._gog_target is not None

    @Property(bool, notify=existingStateChanged)
    def importing(self) -> bool:
        return self._importing

    @Property(str, notify=existingStateChanged)
    def importError(self) -> str:  # noqa: N802
        return self._import_error

    @Property(bool, notify=stateChanged)
    def createsGameFolder(self) -> bool:  # noqa: N802
        return bool(self._selection and self._selection.creates_game_folder)

    @Property(str, notify=stateChanged)
    def directory(self) -> str:
        return self._directory

    @Property(list, notify=stateChanged)
    def runnersToInstall(self) -> list[str]:  # noqa: N802
        return list(self._selection.runners_to_install) if self._selection else []

    @Property(str, notify=stateChanged)
    def requiredGame(self) -> str:  # noqa: N802
        return (self._selection.required_game_slug or "") if self._selection else ""

    @Property(bool, notify=stateChanged)
    def createsShortcuts(self) -> bool:  # noqa: N802
        return self._kind == InstallationKind.INSTALL

    @Property(bool, notify=stateChanged)
    def offersExtras(self) -> bool:  # noqa: N802
        return bool(self._selection and self._selection.offers_extras)

    @Property(bool, notify=stateChanged)
    def canChooseWinePrefix(self) -> bool:  # noqa: N802
        return bool(self._selection and self._selection.can_choose_wine_prefix)

    @Property(str, notify=stateChanged)
    def title(self) -> str:
        if self._kind == InstallationKind.UPDATE:
            return _("Update or repair %s") % self.gameName
        if self._kind == InstallationKind.DLC:
            return _("Install DLC for %s") % self._game_name
        return _("Install %s") % self.gameName

    @Property(str, notify=stateChanged)
    def startLabel(self) -> str:  # noqa: N802
        if self._kind == InstallationKind.UPDATE:
            return _("Apply")
        if self._kind == InstallationKind.DLC:
            return _("Install DLC")
        return _("Install")

    @Slot(str, str, str, str, str)
    def open_installers(self, service: str, appid: str, game_id: str, game_name: str, operation: str) -> None:
        kind = InstallationKind[operation.upper()]
        if kind == InstallationKind.INSTALL and installers.uses_external_installation(service):
            start_task(installers.open_external_installation(service, appid), self._prompts.error)
            return
        if self.installation_controller.active or self._importing:
            return
        self._reset(game_name)
        self._kind = kind
        if service == "gog" and appid and kind == InstallationKind.INSTALL:
            self._gog_target = (appid, game_id)
            self.existingStateChanged.emit()
        request = partial(installers.find_store_installers, service, appid, game_id, kind)
        self._start(partial(self._load, request))
        self.requested.emit()

    @Property(list, constant=True)
    def windowsSetupPresets(self) -> list[dict[str, str]]:  # noqa: N802
        return [{"label": label, "value": value} for label, value in installers.WINDOWS_SETUP_PRESETS]

    @Property(list, constant=True)
    def windowsSetupLocales(self) -> list[dict[str, str]]:  # noqa: N802
        return [{"label": label, "value": value} for label, value in installers.windows_setup_locales()]

    @Slot(str, QUrl, str, str)
    def open_windows_setup(self, game_name: str, executable: QUrl, windows_version: str, locale: str) -> None:
        if self.installation_controller.active or self._importing:
            return
        self._reset(game_name)
        request = partial(
            installers.prepare_windows_setup, game_name, executable.toLocalFile(), windows_version, locale
        )
        self._start(partial(self._load, request))
        self.requested.emit()

    async def open_store_uri(self, service: str, appid: str) -> None:
        if installers.uses_external_installation(service):
            await installers.open_external_installation(service, appid)
            return
        name = await installers.get_store_game_name(service, appid)
        if self.installation_controller.active or self._importing:
            raise QtrisError(_("Finish the current installation first."))
        self.open_installers(service, appid, "", name, "install")

    @Slot(int)
    def select(self, index: int) -> None:
        if self._catalog and not self._task and 0 <= index < len(self._catalog.choices):
            self._start(partial(self._select, self._catalog, index))

    @Slot(QUrl, result=str)
    def local_path(self, url: QUrl) -> str:
        return url.toLocalFile()

    @Slot(str, result=QUrl)
    def folder_url(self, directory: str) -> QUrl:
        return QUrl.fromLocalFile(directory)

    @Slot(str, str, str)
    def import_existing(self, directory: str, executable: str, wine_prefix: str) -> None:
        if self._gog_target is None or self._importing or self.installation_controller.active:
            return
        self._importing = True
        self._import_error = ""
        self.existingStateChanged.emit()
        appid, game_id = self._gog_target
        start_task(
            self._import_gog(appid, game_id, directory.strip(), executable.strip(), wine_prefix.strip()),
            self._prompts.error,
        )

    async def _import_gog(self, appid: str, game_id: str, directory: str, executable: str, wine_prefix: str) -> None:
        try:
            game = await local_games.register_gog_game(
                appid, directory, executable, game_id=game_id, wine_prefix=wine_prefix
            )
        except QtrisError as error:
            self._import_error = str(error)
            return
        finally:
            self._importing = False
            self.existingStateChanged.emit()
        self.existingGameImported.emit(game.name)
        self.closeRequested.emit()

    @Slot(QUrl)
    def choose_directory(self, url: QUrl) -> None:
        self.set_directory(url.toLocalFile())

    @Slot(str)
    def set_directory(self, directory: str) -> None:
        if directory != self._directory:
            self._directory = directory
            self.stateChanged.emit()

    @Slot(bool, bool, bool, str)
    def install(self, desktop_shortcut: bool, menu_shortcut: bool, include_extras: bool, wine_prefix: str) -> None:
        """Start installing with the selected script, into the chosen folder."""
        selection = self._selection
        if not selection or selection.required_game_slug or self._task or self._importing:
            return
        if selection.creates_game_folder and not self._directory.strip():
            return
        kinds = tuple(kind for kind, chosen in (("desktop", desktop_shortcut), ("menu", menu_shortcut)) if chosen)
        self.installation_controller.start(
            selection.catalog_id,
            selection.choice_id,
            self._directory.strip(),
            kinds if self.createsShortcuts else None,
            include_extras=include_extras and selection.offers_extras,
            wine_prefix=wine_prefix.strip() if selection.can_choose_wine_prefix else "",
        )

    @Slot()
    def close_installers(self) -> None:
        self.installation_controller.close()
        self._reset("")

    def _reset(self, game_name: str) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        if self._catalog:
            start_task(installers.close_catalog(self._catalog.id), self._prompts.error)
        self._game_name = game_name
        self._catalog = None
        self.catalogChanged.emit()
        self._selection = None
        self._choice_index = -1
        self._directory = ""
        self._error = ""
        self._kind = InstallationKind.INSTALL
        self._gog_target = None
        self._import_error = ""
        self.existingStateChanged.emit()
        self.stateChanged.emit()

    def _start(self, operation: Callable[[], Coroutine[Any, Any, None]]) -> None:
        self._task = start_task(self._prepare(operation), self._prompts.error)
        self.stateChanged.emit()

    async def _prepare(self, operation: Callable[[], Coroutine[Any, Any, None]]) -> None:
        try:
            await operation()
        except QtrisError as error:
            self._error = str(error)
        except Exception:
            self._error = _("Could not prepare the installation.")
            raise
        finally:
            # A cancelled task must not clear the task that replaced it
            if self._task is asyncio.current_task():
                self._task = None
                self.stateChanged.emit()

    async def _load(self, request: Callable[[], Coroutine[Any, Any, InstallerCatalog]]) -> None:
        self._catalog = catalog = await request()
        self.catalogChanged.emit()
        await self._select(catalog, 0)

    async def _select(self, catalog: InstallerCatalog, index: int) -> None:
        self._choice_index = index
        self._selection = None
        self._directory = ""
        self._error = ""
        self.stateChanged.emit()
        self._selection = await installers.select_installer(catalog.id, catalog.choices[index].id)
        self._directory = self._selection.default_directory
