"""Keep native tray integration separate from the Qt Quick window."""

from __future__ import annotations

import os
import sys
from gettext import gettext as _
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QImage, QWindow

from qtris.backend import events, games
from qtris.qt.tray.dbus_menu import DBusMenu, MenuItem
from qtris.qt.tray.status_notifier import StatusNotifierItem, TrayAvailability, session_bus_address

if TYPE_CHECKING:
    from collections.abc import Callable

    from qtris.qt.preferences import PreferencesController
    from qtris.qt.tray.status_notifier import IconPixmap

ICON_SIZES = (16, 22, 24, 32, 48, 64)
RECENT_GAMES = 10


def icon_pixmaps(icon: QIcon) -> list[IconPixmap]:
    """Return the icon's images as StatusNotifierItem wants them: ARGB32, big-endian."""
    pixmaps: list[IconPixmap] = []
    for size in ICON_SIZES:
        image = icon.pixmap(size, size).toImage().convertToFormat(QImage.Format.Format_ARGB32)
        if image.isNull():
            continue
        pixels = bytearray(bytes(image.constBits())[: image.width() * image.height() * 4])
        if sys.byteorder == "little":
            # Qt keeps each pixel as a native-endian 32-bit integer
            pixels[0::4], pixels[1::4], pixels[2::4], pixels[3::4] = (
                pixels[3::4],
                pixels[2::4],
                pixels[1::4],
                pixels[0::4],
            )
        pixmaps.append((image.width(), image.height(), bytes(pixels)))
    return pixmaps


class TrayIcon(QObject):
    """The tray icon and its menu: recent games, showing or hiding Qtris, and quitting.
    Clicking the icon opens the menu."""

    disabled = Signal()

    def __init__(
        self,
        preferences: PreferencesController,
        window: QWindow,
        show_window: Callable[[], None],
        launch_game: Callable[[str], None],
        quit_application: Callable[[], None],
        bus_address: str | None = None,
    ) -> None:
        super().__init__(window)
        self._preferences = preferences
        self._window = window
        self._show_window = show_window
        self._launch_game = launch_game
        self._closed = False
        self._game_entries: list[tuple[str, str]] = []
        self._game_items: list[MenuItem] = []
        self._separator = MenuItem(separator=True, visible=False)
        self._window_item = MenuItem(on_click=self._toggle_window)
        self._quit_item = MenuItem(_("Quit Qtris"), on_click=quit_application)
        self._menu = DBusMenu()
        self._window.visibilityChanged.connect(self._sync_window_action)

        # The desktop may cache the exported menu before opening it
        self._menu_refresh_timer = QTimer(self, singleShot=True)
        self._menu_refresh_timer.timeout.connect(self._refresh_games)
        self._registrations = [
            source.register(self._schedule_menu_refresh)
            for source in (events.GAME_CHANGED, events.GAME_INSTALLED, events.GAME_REMOVED, events.CATEGORIES_CHANGED)
        ]
        address = bus_address or session_bus_address()
        self._icon = StatusNotifierItem(
            "Qtris", _("Qtris"), icon_pixmaps(QGuiApplication.windowIcon()), self._menu, self._show_from_tray, address
        )
        self._availability = TrayAvailability(address, self._preferences.set_tray_available)
        self._preferences.interfaceChanged.connect(self._sync)
        self._sync_window_action()
        self._sync()

    @property
    def menu_items(self) -> tuple[MenuItem, ...]:
        return self._menu.items

    def can_keep_open(self) -> bool:
        return self._icon.is_visible and self._preferences.is_tray_available()

    def _sync(self) -> None:
        enabled = self._preferences.is_tray_enabled()
        if enabled == self._icon.is_visible:
            return
        if enabled:
            self._refresh_games()
            self._icon.show()
        else:
            self._icon.hide()
            self.disabled.emit()

    def _schedule_menu_refresh(self, *_args: object) -> None:
        if not self._closed and self._icon.is_visible:
            self._menu_refresh_timer.start()

    def _refresh_games(self) -> None:
        entries = games.list_recent_installed_games(RECENT_GAMES)
        if entries == self._game_entries and self._menu.items:
            return
        self._game_entries = entries
        self._game_items = [
            MenuItem(name, on_click=lambda game_id=game_id: self._launch_game(game_id)) for game_id, name in entries
        ]
        self._separator.visible = bool(entries)
        self._menu.set_items([*self._game_items, self._separator, self._window_item, self._quit_item])

    def _is_window_shown(self) -> bool:
        return self._window.isVisible() and self._window.visibility() != QWindow.Visibility.Minimized

    def _sync_window_action(self) -> None:
        label = _("Hide Qtris") if self._is_window_shown() else _("Show Qtris")
        if label != self._window_item.label:
            self._window_item.label = label
            self._menu.changed()

    def _show_from_tray(self) -> None:
        # Qt's Wayland backend activates the window with this token, then forgets it
        if token := self._icon.activation_token:
            self._icon.activation_token = ""
            os.environ["XDG_ACTIVATION_TOKEN"] = token
        self._show_window()

    def _toggle_window(self) -> None:
        if self._is_window_shown():
            self._window.hide()
        else:
            self._show_from_tray()

    def shutdown(self) -> None:
        self._closed = True
        self._menu_refresh_timer.stop()
        for registration in self._registrations:
            registration.unregister()
        self._preferences.interfaceChanged.disconnect(self._sync)
        self._icon.hide()
        self._availability.close()
