"""Keep native tray integration separate from the Qt Quick window."""

from __future__ import annotations

import asyncio
from gettext import gettext as _
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QWindow
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from qtris.backend import events, games
from qtris.util.log import logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from qtris.qt.preferences import PreferencesController


class TrayIcon(QObject):
    disabled = Signal()

    def __init__(
        self,
        preferences: PreferencesController,
        window: QWindow,
        show_window: Callable[[], None],
        launch_game: Callable[[str], None],
        quit_application: Callable[[], None],
    ) -> None:
        super().__init__(window)
        self._preferences = preferences
        self._window = window
        self._show_window = show_window
        self._launch_game = launch_game
        self._closed = False
        self._menu = QMenu()
        self._game_actions: list[QAction] = []
        self._game_entries: list[tuple[str, str]] = []
        self._game_separator = self._menu.addSeparator()
        self._game_separator.setVisible(False)
        self._show_action = self._menu.addAction(_("Show Qtris"))
        self._show_action.triggered.connect(lambda: show_window())
        self._hide_action = self._menu.addAction(_("Hide Qtris"))
        self._hide_action.triggered.connect(window.hide)
        self._menu.addAction(_("Quit Qtris")).triggered.connect(lambda: quit_application())
        self._window.visibilityChanged.connect(self._sync_window_actions)
        self._sync_window_actions()

        # The desktop may cache the exported menu before opening it
        self._menu_dirty = False
        self._menu_refresh_task: asyncio.Task[None] | None = None
        self._registrations = [
            source.register(self._schedule_menu_refresh)
            for source in (events.GAME_CHANGED, events.GAME_INSTALLED, events.GAME_REMOVED, events.CATEGORIES_CHANGED)
        ]
        self._icon = QSystemTrayIcon(QGuiApplication.windowIcon(), self)
        self._icon.setContextMenu(self._menu)
        self._icon.setToolTip(_("Qtris"))
        self._icon.activated.connect(self._on_activated)
        self._availability_timer = QTimer(self, interval=1000, timerType=Qt.TimerType.VeryCoarseTimer)
        self._availability_timer.timeout.connect(self.refresh_availability)
        self._preferences.interfaceChanged.connect(self._sync)
        self.refresh_availability()
        self._sync()
        self._availability_timer.start()
        self._window.installEventFilter(self)

    def refresh_availability(self) -> None:
        self._preferences.set_tray_available(QSystemTrayIcon.isSystemTrayAvailable())

    def can_keep_open(self) -> bool:
        return self._icon.isVisible() and self._preferences.is_tray_available()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._window and event.type() == QEvent.Type.Close and self.can_keep_open():
            # An accepted close would destroy the native window
            event.ignore()
            self._window.hide()
            return True
        return super().eventFilter(watched, event)

    def _sync(self) -> None:
        enabled = self._preferences.is_tray_enabled()
        if enabled == self._icon.isVisible():
            return
        # Qt registers visible icons when the desktop tray becomes available
        self._icon.setVisible(enabled)
        if enabled:
            self._schedule_menu_refresh()
        else:
            self.disabled.emit()

    def _schedule_menu_refresh(self, *_args: object) -> None:
        if not self._closed and self._icon.isVisible():
            self._menu_dirty = True
            if self._menu_refresh_task is None or self._menu_refresh_task.done():
                self._menu_refresh_task = asyncio.create_task(self._refresh_games())

    async def _refresh_games(self) -> None:
        while self._menu_dirty and self._icon.isVisible():
            self._menu_dirty = False
            try:
                entries = await asyncio.to_thread(games.list_recent_installed_games, 10)
            except Exception:
                logger.exception("Could not refresh the system tray game list")
                continue
            if self._icon.isVisible() and not self._menu_dirty:
                self._set_game_actions(entries)

    def _set_game_actions(self, entries: list[tuple[str, str]]) -> None:
        if entries == self._game_entries:
            return
        self._game_entries = entries
        for action in self._game_actions:
            self._menu.removeAction(action)
            action.deleteLater()
        self._game_actions.clear()
        for game_id, name in entries:
            # Treat ampersands in game titles as text, not menu mnemonics
            action = QAction(name.replace("&", "&&"), self._menu)
            action.triggered.connect(lambda checked=False, game_id=game_id: self._launch_game(game_id))
            self._menu.insertAction(self._game_separator, action)
            self._game_actions.append(action)
        self._game_separator.setVisible(bool(entries))

    def _sync_window_actions(self) -> None:
        self._hide_action.setVisible(
            self._window.isVisible() and self._window.visibility() != QWindow.Visibility.Minimized
        )

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    def shutdown(self) -> None:
        self._closed = True
        self._window.removeEventFilter(self)
        self._window.visibilityChanged.disconnect(self._sync_window_actions)
        self._availability_timer.stop()
        if self._menu_refresh_task is not None:
            self._menu_refresh_task.cancel()
        for registration in self._registrations:
            registration.unregister()
        self._preferences.interfaceChanged.disconnect(self._sync)
        self._icon.hide()
        self._menu.close()
