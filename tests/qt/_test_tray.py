"""Exercise the Qt tray as desktop availability changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QSystemTrayIcon

from qtris import settings
from qtris.qt.delegates import PromptController
from qtris.qt.preferences import PreferencesController
from qtris.qt.tray import TrayIcon

if TYPE_CHECKING:
    from PySide6.QtGui import QGuiApplication
    from pytest import MonkeyPatch


def test_tray_follows_desktop_availability(qt_application: QGuiApplication, monkeypatch: MonkeyPatch) -> None:
    available = False
    enabled = True
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: available)
    monkeypatch.setattr(settings, "read_bool_setting", lambda _key: enabled)
    parent = QObject()
    preferences = PreferencesController(PromptController(), parent)
    holds: list[bool] = []
    tray = TrayIcon(preferences, lambda: None, lambda: None, holds.append, parent)

    try:
        assert tray._icon.isVisible()
        assert not preferences.is_tray_available()
        assert holds == [False]

        available = True
        tray._availability_timer.timeout.emit()
        assert preferences.is_tray_available()
        assert holds[-1] is True

        available = False
        tray._availability_timer.timeout.emit()
        assert not preferences.is_tray_available()
        assert holds[-1] is False
        assert tray._icon.isVisible()

        enabled = False
        preferences.interfaceChanged.emit()
        available = True
        tray._availability_timer.timeout.emit()
        assert not tray._icon.isVisible()
        assert holds[-1] is False

        enabled = True
        preferences.interfaceChanged.emit()
        assert tray._icon.isVisible()
        assert holds[-1] is True
    finally:
        tray.shutdown()

    assert not tray._availability_timer.isActive()
    assert not tray._icon.isVisible()
    assert holds[-1] is False
