"""Keep native tray integration separate from the Qt Quick window."""

from __future__ import annotations

from gettext import gettext as _
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QWindow
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from qtris.backend import events, games

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
        self._window_action = self._menu.addAction("")
        self._window_action.triggered.connect(self._toggle_window)
        self._menu.addAction(_("Quit Qtris")).triggered.connect(lambda: quit_application())
        self._window.visibilityChanged.connect(self._sync_window_action)
        self._sync_window_action()

        # The desktop may cache the exported menu before opening it
        self._menu_refresh_timer = QTimer(self, singleShot=True)
        self._menu_refresh_timer.timeout.connect(self._refresh_games)
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

    def refresh_availability(self) -> None:
        self._preferences.set_tray_available(QSystemTrayIcon.isSystemTrayAvailable())

    def can_keep_open(self) -> bool:
        self.refresh_availability()
        return self._icon.isVisible() and self._preferences.is_tray_available()

    def _sync(self) -> None:
        enabled = self._preferences.is_tray_enabled()
        if enabled == self._icon.isVisible():
            return
        if enabled:
            self._refresh_games()
        # Qt registers visible icons when the desktop tray becomes available
        self._icon.setVisible(enabled)
        if not enabled:
            self.disabled.emit()

    def _schedule_menu_refresh(self, *_args: object) -> None:
        if not self._closed and self._icon.isVisible():
            self._menu_refresh_timer.start()

    def _refresh_games(self) -> None:
        entries = games.list_recent_installed_games(10)
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

    def _is_window_shown(self) -> bool:
        return self._window.isVisible() and self._window.visibility() != QWindow.Visibility.Minimized

    def _sync_window_action(self) -> None:
        self._window_action.setText(_("Hide Qtris") if self._is_window_shown() else _("Show Qtris"))

    def _toggle_window(self) -> None:
        if self._is_window_shown():
            self._window.hide()
        else:
            self._show_window()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_window()

    def shutdown(self) -> None:
        self._closed = True
        self._availability_timer.stop()
        self._menu_refresh_timer.stop()
        for registration in self._registrations:
            registration.unregister()
        self._preferences.interfaceChanged.disconnect(self._sync)
        self._icon.hide()
        self._menu.close()
