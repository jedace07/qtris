"""Exercise the tray over D-Bus as the desktop's tray sees it, on a private bus."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest
from gi.repository import Gio, GLib
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QIcon, QPixmap, QWindow

from qtris import settings
from qtris.backend import events, games
from qtris.qt import application
from qtris.qt.delegates import PromptController
from qtris.qt.preferences import PreferencesController
from qtris.qt.tray import TrayIcon
from qtris.qt.tray import icon as tray_icon
from qtris.qt.tray.icon import icon_pixmaps
from qtris.qt.tray.status_notifier import (
    CONNECTION_FLAGS,
    ITEM_INTERFACE,
    ITEM_PATH,
    WATCHER_INTERFACE,
    WATCHER_NAME,
    WATCHER_PATH,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from PySide6.QtGui import QGuiApplication
    from pytest import MonkeyPatch

    from tests.conftest import FakeMainLoop

# Iterating GLib's loop imports PyGObject's asyncio integration, as run_asyncio_on_glib() does quietly
pytestmark = [
    pytest.mark.filterwarnings("ignore::DeprecationWarning:gi.events"),
    pytest.mark.skipif(shutil.which("dbus-daemon") is None, reason="needs dbus-daemon for a private bus"),
]

WATCHER_XML = f"""<node>
  <interface name="{WATCHER_INTERFACE}">
    <method name="RegisterStatusNotifierItem"><arg type="s" name="service" direction="in"/></method>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <signal name="StatusNotifierHostRegistered"/>
    <signal name="StatusNotifierHostUnregistered"/>
  </interface>
</node>"""


def wait_until(condition: Callable[[], object], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    context = GLib.MainContext.default()
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("Timed out waiting for the tray")
        context.iteration(False)
        QCoreApplication.processEvents()
        time.sleep(0.005)


class FakeDesktop:
    """The desktop's StatusNotifierWatcher and tray host, which reads the icon as Plasma does."""

    def __init__(self, address: str) -> None:
        self.connection = Gio.DBusConnection.new_for_address_sync(address, CONNECTION_FLAGS, None, None)
        self.items: list[str] = []
        self.host_registered = True
        interface = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0]
        self._registration = self.connection.register_object(WATCHER_PATH, interface, self._call, self._get, None)
        self._owner = Gio.bus_own_name_on_connection(
            self.connection, WATCHER_NAME, Gio.BusNameOwnerFlags.NONE, None, None
        )
        wait_until(lambda: self.has_owner(WATCHER_NAME))

    def _call(self, *args: Any) -> None:
        sender, method, parameters, invocation = args[1], args[4], args[5], args[6]
        if method == "RegisterStatusNotifierItem":
            (service,) = parameters.unpack()
            self.items.append(sender + service if service.startswith("/") else service)
            invocation.return_value(None)

    def _get(self, *_args: Any) -> GLib.Variant:
        # The only property the watcher declares here
        return GLib.Variant("b", self.host_registered)

    def set_host(self, registered: bool) -> None:
        self.host_registered = registered
        name = "StatusNotifierHostRegistered" if registered else "StatusNotifierHostUnregistered"
        self.connection.emit_signal(None, WATCHER_PATH, WATCHER_INTERFACE, name, None)

    def call(self, name: str, path: str, interface: str, method: str, parameters: GLib.Variant | None) -> Any:
        """Call and wait while the loop runs, since the icon answers on this thread."""
        results: list[Any] = []

        def answered(_source: object, result: Gio.AsyncResult) -> None:
            try:
                results.append(self.connection.call_finish(result).unpack())
            except GLib.Error as error:
                results.append(error)

        self.connection.call(
            name, path, interface, method, parameters, None, Gio.DBusCallFlags.NONE, 5000, None, answered
        )
        wait_until(lambda: results)
        if isinstance(results[0], GLib.Error):
            raise results[0]
        return results[0]

    def has_owner(self, name: str) -> bool:
        (owned,) = self.call(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (name,)),
        )
        return owned

    @property
    def icon_name(self) -> str:
        return self.items[-1].removesuffix(ITEM_PATH)

    def icon_property(self, name: str) -> Any:
        (value,) = self.call(
            self.icon_name,
            ITEM_PATH,
            "org.freedesktop.DBus.Properties",
            "Get",
            GLib.Variant("(ss)", (ITEM_INTERFACE, name)),
        )
        return value

    def menu(self) -> list[tuple[int, dict[str, Any]]]:
        _revision, (_root, _properties, children) = self.call(
            self.icon_name, "/MenuBar", "com.canonical.dbusmenu", "GetLayout", GLib.Variant("(iias)", (0, -1, []))
        )
        return [(item_id, properties) for item_id, properties, _children in children]

    def labels(self) -> list[str]:
        return [
            properties.get("label", "---" if properties.get("type") == "separator" else "")
            for _item_id, properties in self.menu()
            if properties.get("visible", True)
        ]

    def click(self, label: str) -> None:
        item_id = next(item_id for item_id, properties in self.menu() if properties.get("label") == label)
        self.click_id(item_id)

    def click_id(self, item_id: int) -> None:
        self.call(
            self.icon_name,
            "/MenuBar",
            "com.canonical.dbusmenu",
            "Event",
            GLib.Variant("(isvu)", (item_id, "clicked", GLib.Variant("i", 0), 0)),
        )

    def activate(self) -> None:
        self.call(self.icon_name, ITEM_PATH, ITEM_INTERFACE, "Activate", GLib.Variant("(ii)", (0, 0)))

    def close(self) -> None:
        Gio.bus_unown_name(self._owner)
        self.connection.unregister_object(self._registration)
        self.connection.close_sync(None)


@pytest.fixture
def bus_address(monkeypatch: MonkeyPatch) -> Iterator[str]:
    """A private session bus, which the tray uses when it isn't given one."""
    daemon = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address"], stdout=subprocess.PIPE, text=True
    )
    assert daemon.stdout is not None
    address = daemon.stdout.readline().strip()
    monkeypatch.setattr(tray_icon, "session_bus_address", lambda: address)
    try:
        yield address
    finally:
        daemon.terminate()
        daemon.wait(5)


@pytest.fixture
def desktop(qt_application: QGuiApplication, bus_address: str) -> Iterator[FakeDesktop]:
    # Qtris always has a window icon, which the tray icon shows
    previous_icon = qt_application.windowIcon()
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor("teal"))
    qt_application.setWindowIcon(QIcon(pixmap))
    fake = FakeDesktop(bus_address)
    yield fake
    fake.close()
    qt_application.setWindowIcon(previous_icon)


def test_tray_follows_desktop_availability(
    qt_application: QGuiApplication, monkeypatch: MonkeyPatch, bus_address: str, desktop: FakeDesktop
) -> None:
    enabled = True
    monkeypatch.setattr(settings, "read_bool_setting", lambda _key: enabled)
    monkeypatch.setattr(games, "list_recent_installed_games", lambda _limit: [])
    window = QWindow()
    preferences = PreferencesController(PromptController(), window)
    tray = TrayIcon(preferences, window, window.show, lambda _game_id: None, lambda: None, bus_address)
    disabled: list[bool] = []
    tray.disabled.connect(lambda: disabled.append(True))

    try:
        wait_until(lambda: desktop.items and preferences.is_tray_available())
        assert tray.can_keep_open()
        assert desktop.icon_property("ItemIsMenu") is True
        assert desktop.icon_property("Menu") == "/MenuBar"
        assert desktop.icon_property("IconPixmap")
        first_icon = desktop.icon_name

        desktop.set_host(False)
        wait_until(lambda: not preferences.is_tray_available())
        assert not tray.can_keep_open()
        assert desktop.has_owner(first_icon)
        assert not disabled

        enabled = False
        preferences.interfaceChanged.emit()
        desktop.set_host(True)
        wait_until(lambda: preferences.is_tray_available())
        # The icon's own connection closes, which takes it off the tray
        wait_until(lambda: not desktop.has_owner(first_icon))
        assert not tray.can_keep_open()
        assert disabled == [True]

        enabled = True
        preferences.interfaceChanged.emit()
        wait_until(lambda: len(desktop.items) == 2)
        assert desktop.icon_name != first_icon
        assert tray.can_keep_open()
    finally:
        tray.shutdown()

    second_icon = desktop.icon_name
    wait_until(lambda: not desktop.has_owner(second_icon))


def test_a_watcher_restart_registers_the_icon_again(
    qt_application: QGuiApplication, monkeypatch: MonkeyPatch, bus_address: str
) -> None:
    monkeypatch.setattr(settings, "read_bool_setting", lambda _key: True)
    monkeypatch.setattr(games, "list_recent_installed_games", lambda _limit: [])
    window = QWindow()
    preferences = PreferencesController(PromptController(), window)
    desktop = FakeDesktop(bus_address)
    tray = TrayIcon(preferences, window, window.show, lambda _game_id: None, lambda: None, bus_address)
    try:
        wait_until(lambda: desktop.items and preferences.is_tray_available())
        desktop.close()
        wait_until(lambda: not preferences.is_tray_available())
        desktop = FakeDesktop(bus_address)
        wait_until(lambda: desktop.items and preferences.is_tray_available())
    finally:
        tray.shutdown()
        desktop.close()


def test_clicking_opens_a_menu_of_recent_games(
    qt_application: QGuiApplication,
    monkeypatch: MonkeyPatch,
    fake_main_loop: FakeMainLoop,
    bus_address: str,
    desktop: FakeDesktop,
) -> None:
    entries = [("12", "March & Climb"), ("34", "Snake_Case")]
    monkeypatch.setattr(games, "list_recent_installed_games", lambda limit: entries[:limit])
    monkeypatch.setattr(settings, "read_bool_setting", lambda _key: True)
    window = QWindow()
    launched: list[str] = []
    preferences = PreferencesController(PromptController(), window)
    tray = TrayIcon(preferences, window, window.showNormal, launched.append, lambda: None, bus_address)
    try:
        wait_until(lambda: desktop.items)
        # Underscores mark access keys in DBusMenu labels, so they're doubled
        assert desktop.labels() == ["March & Climb", "Snake__Case", "---", "Show Qtris", "Quit Qtris"]
        desktop.click("March & Climb")
        desktop.click("Snake__Case")
        wait_until(lambda: launched == ["12", "34"])

        desktop.click("Show Qtris")
        wait_until(window.isVisible)
        assert desktop.labels()[3] == "Hide Qtris"
        window.showMinimized()
        assert desktop.labels()[3] == "Show Qtris"
        # A tray that sends clicks to the icon shows the window, however often it's clicked
        desktop.activate()
        wait_until(lambda: window.visibility() == QWindow.Visibility.Windowed)
        desktop.activate()
        desktop.activate()
        wait_until(lambda: desktop.labels()[3] == "Hide Qtris")
        assert window.isVisible()
        desktop.click("Hide Qtris")
        wait_until(lambda: not window.isVisible())

        stale_id = desktop.menu()[0][0]
        entries[:] = [("34", "Renamed")]
        events.GAME_REMOVED.fire("12")
        fake_main_loop.run_pending()
        wait_until(lambda: desktop.labels()[0] == "Renamed")
        # A click on an entry the desktop showed before the menu changed reaches nothing
        with pytest.raises(GLib.Error):
            desktop.click_id(stale_id)
        desktop.click("Renamed")
        wait_until(lambda: launched == ["12", "34", "34"])

        entries.clear()
        events.CATEGORIES_CHANGED.fire()
        fake_main_loop.run_pending()
        wait_until(lambda: desktop.labels() == ["Show Qtris", "Quit Qtris"])
    finally:
        tray.shutdown()


def test_the_desktops_activation_token_reaches_the_window(
    qt_application: QGuiApplication, monkeypatch: MonkeyPatch, bus_address: str, desktop: FakeDesktop
) -> None:
    monkeypatch.setattr(settings, "read_bool_setting", lambda _key: True)
    monkeypatch.setattr(games, "list_recent_installed_games", lambda _limit: [])
    monkeypatch.delenv("XDG_ACTIVATION_TOKEN", raising=False)
    window = QWindow()
    tokens: list[str | None] = []
    preferences = PreferencesController(PromptController(), window)
    tray = TrayIcon(
        preferences,
        window,
        lambda: tokens.append(os.environ.get("XDG_ACTIVATION_TOKEN")),
        lambda _game_id: None,
        lambda: None,
        bus_address,
    )
    try:
        wait_until(lambda: desktop.items)
        desktop.call(
            desktop.icon_name,
            ITEM_PATH,
            ITEM_INTERFACE,
            "ProvideXdgActivationToken",
            GLib.Variant("(s)", ("token-1",)),
        )
        desktop.click("Show Qtris")
        wait_until(lambda: tokens)
        assert tokens == ["token-1"]
        # Each token is used once; Qt's Wayland backend unsets the variable when it does
        assert tray._icon.activation_token == ""
    finally:
        tray.shutdown()
        os.environ.pop("XDG_ACTIVATION_TOKEN", None)


def test_icon_pixmaps_are_big_endian_argb(qt_application: QGuiApplication) -> None:
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor(0x11, 0x22, 0x33, 0xFF))
    width, height, pixels = next(image for image in icon_pixmaps(QIcon(pixmap)) if image[0] == 16)
    assert (width, height) == (16, 16)
    assert pixels[:4] == bytes([0xFF, 0x11, 0x22, 0x33])
    assert len(pixels) == 16 * 16 * 4


def test_closing_to_tray_holds_application_through_watcher_loss(
    qt_application: QGuiApplication, monkeypatch: MonkeyPatch, bus_address: str, desktop: FakeDesktop
) -> None:
    enabled = True
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
        wait_until(lambda: desktop.items and preferences.is_tray_available())
        assert app._window_held and not app._tray_held
        window.close()
        assert app._tray_held and not app._window_held
        assert hold.call_count - release.call_count == 1

        desktop.set_host(False)
        wait_until(lambda: not preferences.is_tray_available())
        assert app._tray_held
        assert hold.call_count - release.call_count == 1

        desktop.set_host(True)
        wait_until(lambda: preferences.is_tray_available())
        desktop.click("Show Qtris")
        wait_until(lambda: app._window_held)
        assert not app._tray_held
        assert hold.call_count - release.call_count == 1

        window.close()
        desktop.click("Installed Game")
        wait_until(lambda: controller.launch.called)
        controller.launch.assert_called_once_with("123", False)
        assert window.isVisible()

        window.close()
        enabled = False
        preferences.interfaceChanged.emit()
        assert not app._tray_held
        assert hold.call_count == release.call_count

        enabled = True
        desktop.set_host(False)
        wait_until(lambda: not preferences.is_tray_available())
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
