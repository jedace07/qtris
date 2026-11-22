"""Exercise the Qt tray as desktop availability changes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import Mock

from gi.repository import Gio
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QSystemTrayIcon

from qtris import settings
from qtris.backend import events, games
from qtris.qt import application
from qtris.qt.delegates import PromptController
from qtris.qt.preferences import PreferencesController
from qtris.qt.tray import TrayIcon

if TYPE_CHECKING:
    from PySide6.QtGui import QGuiApplication
    from pytest import MonkeyPatch

    from tests.conftest import FakeMainLoop


def test_tray_follows_desktop_availability(qt_application: QGuiApplication, monkeypatch: MonkeyPatch) -> None:
    available = False
    enabled = True
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: available)
    monkeypatch.setattr(settings, "read_bool_setting", lambda _key: enabled)
    monkeypatch.setattr(games, "list_recent_installed_games", lambda _limit: [])
    window = QWindow()
    preferences = PreferencesController(PromptController(), window)
    tray = TrayIcon(preferences, window, window.show, lambda _game_id: None, lambda: None)
    disabled: list[bool] = []
    tray.disabled.connect(lambda: disabled.append(True))

    try:
        assert tray._icon.isVisible()
        assert not preferences.is_tray_available()
        assert not tray.can_keep_open()

        available = True
        tray._availability_timer.timeout.emit()
        assert preferences.is_tray_available()
        assert tray.can_keep_open()

        available = False
        tray._availability_timer.timeout.emit()
        assert not preferences.is_tray_available()
        assert not tray.can_keep_open()
        assert tray._icon.isVisible()
        assert not disabled

        enabled = False
        preferences.interfaceChanged.emit()
        available = True
        tray._availability_timer.timeout.emit()
        assert not tray._icon.isVisible()
        assert not tray.can_keep_open()
        assert disabled == [True]

        enabled = True
        preferences.interfaceChanged.emit()
        assert tray._icon.isVisible()
        assert tray.can_keep_open()
    finally:
        tray.shutdown()

    assert not tray._availability_timer.isActive()
    assert not tray._icon.isVisible()


def test_tray_game_actions_refresh_and_launch_by_id(
    qt_application: QGuiApplication, monkeypatch: MonkeyPatch, fake_main_loop: FakeMainLoop
) -> None:
    entries = [("12", "March & Climb"), ("34", "Another Game")]
    monkeypatch.setattr(games, "list_recent_installed_games", lambda limit: entries[:limit])
    monkeypatch.setattr(settings, "read_bool_setting", lambda _key: True)
    window = QWindow()
    launched: list[str] = []
    preferences = PreferencesController(PromptController(), window)
    tray = TrayIcon(preferences, window, window.showNormal, launched.append, lambda: None)
    try:
        menu = tray._icon.contextMenu()
        assert menu is not None
        first, second, separator, toggle, _quit = menu.actions()
        assert [first.text(), second.text(), toggle.text()] == ["March && Climb", "Another Game", "Show Qtris"]
        assert separator.isVisible()
        first.trigger()
        second.trigger()
        assert launched == ["12", "34"]

        toggle.trigger()
        assert window.isVisible()
        assert toggle.text() == "Hide Qtris"
        window.showMinimized()
        assert toggle.text() == "Show Qtris"
        tray._icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        assert window.visibility() == QWindow.Visibility.Windowed
        toggle.trigger()
        assert not window.isVisible()

        entries[:] = [("34", "Renamed")]
        events.GAME_REMOVED.fire("12")
        fake_main_loop.run_pending()
        qt_application.processEvents()
        assert menu.actions()[0].text() == "Renamed"
        menu.actions()[0].trigger()
        assert launched == ["12", "34", "34"]

        entries.clear()
        events.CATEGORIES_CHANGED.fire()
        fake_main_loop.run_pending()
        qt_application.processEvents()
        assert not separator.isVisible()
        assert [action.text() for action in menu.actions() if action.isVisible()] == ["Show Qtris", "Quit Qtris"]
    finally:
        tray.shutdown()


def test_closing_to_tray_holds_application_through_watcher_loss(
    qt_application: QGuiApplication, monkeypatch: MonkeyPatch
) -> None:
    available = True
    enabled = True
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: available)
    monkeypatch.setattr(games, "list_recent_installed_games", lambda _limit: [("123", "Installed Game")])
    monkeypatch.setattr(settings, "read_bool_setting", lambda _key: enabled)
    window = QWindow()
    prompts = PromptController()
    preferences = PreferencesController(prompts, window)
    controller = Mock(preferences_controller=preferences)
    main_window = SimpleNamespace(
        window=window, prompts=prompts, controller=controller, show=window.show, engine=Mock()
    )
    monkeypatch.setattr(application, "MainWindow", lambda: main_window)
    previous_app = Gio.Application.get_default()
    app = application.QtrisApplication()
    hold, release = Mock(), Mock()
    monkeypatch.setattr(app, "hold", hold)
    monkeypatch.setattr(app, "release", release)
    app.show_window()
    tray = app.tray
    assert tray is not None
    try:
        assert app._window_held and not app._tray_held
        window.close()
        assert app._tray_held and not app._window_held
        assert hold.call_count - release.call_count == 1

        available = False
        tray.refresh_availability()
        assert not preferences.is_tray_available()
        assert app._tray_held
        assert hold.call_count - release.call_count == 1

        available = True
        tray.refresh_availability()
        tray._window_action.trigger()
        assert app._window_held and not app._tray_held
        assert hold.call_count - release.call_count == 1

        window.close()
        tray._game_actions[0].trigger()
        controller.launch.assert_called_once_with("123", False)
        assert window.isVisible()

        window.close()
        enabled = False
        preferences.interfaceChanged.emit()
        assert not app._tray_held
        assert hold.call_count == release.call_count

        enabled = True
        available = False
        preferences.interfaceChanged.emit()
        app.show_window()
        window.close()
        assert not app._tray_held
        assert hold.call_count == release.call_count
    finally:
        window.visibleChanged.disconnect(app.on_window_visibility_changed)
        tray.disabled.disconnect()
        tray.shutdown()
        if previous_app is not None:
            previous_app.set_default()
