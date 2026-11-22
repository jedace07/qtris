"""Keep native tray integration separate from the Qt Quick window."""

from __future__ import annotations

from gettext import gettext as _
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

if TYPE_CHECKING:
    from collections.abc import Callable

    from qtris.qt.preferences import PreferencesController


class TrayIcon(QObject):
    def __init__(
        self,
        preferences: PreferencesController,
        show_window: Callable[[], None],
        quit_application: Callable[[], None],
        set_held: Callable[[bool], None],
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self._preferences = preferences
        self._set_held = set_held
        self._menu = QMenu()
        self._menu.addAction(_("Show Qtris")).triggered.connect(lambda: show_window())
        self._menu.addAction(_("Quit Qtris")).triggered.connect(lambda: quit_application())
        self._icon = QSystemTrayIcon(QGuiApplication.windowIcon(), self)
        self._icon.setContextMenu(self._menu)
        self._icon.setToolTip(_("Qtris"))
        self._icon.activated.connect(
            lambda reason: show_window() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        self._availability_timer = QTimer(self, interval=1000, timerType=Qt.TimerType.VeryCoarseTimer)
        self._availability_timer.timeout.connect(self.refresh_availability)
        self.refresh_availability()
        self._preferences.interfaceChanged.connect(self._sync)
        self._sync()
        self._availability_timer.start()

    def refresh_availability(self) -> None:
        self._preferences.set_tray_available(QSystemTrayIcon.isSystemTrayAvailable())

    def _sync(self) -> None:
        enabled = self._preferences.is_tray_enabled()
        # Qt registers visible icons when the desktop tray becomes available
        self._icon.setVisible(enabled)
        self._set_held(enabled and self._preferences.is_tray_available())

    def shutdown(self) -> None:
        self._availability_timer.stop()
        self._preferences.interfaceChanged.disconnect(self._sync)
        self._icon.hide()
        self._set_held(False)
