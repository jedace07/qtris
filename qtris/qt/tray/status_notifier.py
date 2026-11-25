"""A tray icon over D-Bus with the StatusNotifierItem protocol.

Qt's own tray icon tells the desktop that clicks go to the application rather than
to its menu (ItemIsMenu false), so a click could only show or hide the window. This
icon says it is a menu, as AppIndicator icons do, so clicking it opens the menu.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gi.repository import Gio, GLib

from qtris.qt.tray.dbus_menu import MENU_PATH
from qtris.util.log import logger

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from qtris.qt.tray.dbus_menu import DBusMenu

ITEM_PATH = "/StatusNotifierItem"
ITEM_INTERFACE = "org.kde.StatusNotifierItem"
WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_INTERFACE = "org.kde.StatusNotifierWatcher"
CONNECTION_FLAGS = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION

# Width, height and ARGB32 pixels in network byte order
type IconPixmap = tuple[int, int, bytes]

_NODE_INFO = Gio.DBusNodeInfo.new_for_xml(
    f"""<node>
  <interface name="{ITEM_INTERFACE}">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ProvideXdgActivationToken">
      <arg type="s" name="token" direction="in"/>
    </method>
    <method name="ContextMenu">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="Activate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="Scroll">
      <arg type="i" name="delta" direction="in"/>
      <arg type="s" name="orientation" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus">
      <arg type="s" name="status"/>
    </signal>
    <signal name="NewMenu"/>
  </interface>
</node>"""
)


def session_bus_address() -> str | None:
    """Return the session bus's address, or None without one."""
    try:
        return Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
    except GLib.Error as error:
        logger.debug("No session bus for the tray: %s", error.message)
        return None


class _Connection:
    """A D-Bus connection opened in the background, which can be dropped before it opens."""

    def __init__(self, address: str, on_open: Callable[[Gio.DBusConnection], None]) -> None:
        self.connection: Gio.DBusConnection | None = None
        self._cancellable = Gio.Cancellable()
        self._on_open = on_open
        Gio.DBusConnection.new_for_address(address, CONNECTION_FLAGS, None, self._cancellable, self._opened)

    def _opened(self, _source: object, result: Gio.AsyncResult) -> None:
        try:
            connection = Gio.DBusConnection.new_for_address_finish(result)
        except GLib.Error as error:
            if not self._cancellable.is_cancelled():
                logger.warning("Could not connect the tray to the session bus: %s", error.message)
            return
        if self._cancellable.is_cancelled():
            connection.close(None, None)
            return
        self.connection = connection
        self._on_open(connection)

    def close(self) -> None:
        self._cancellable.cancel()
        if self.connection is not None and not self.connection.is_closed():
            # Closing ends this connection's name, which takes the icon off the tray
            self.connection.close(None, None)
        self.connection = None


class TrayAvailability:
    """Follows whether the desktop shows tray icons: a watcher runs and a tray
    (its host) is registered with it."""

    def __init__(self, address: str | None, on_change: Callable[[bool], None]) -> None:
        self.available = False
        self._on_change = on_change
        self._watch = 0
        self._subscriptions: list[int] = []
        self._connection = _Connection(address, self._opened) if address else None

    def _opened(self, connection: Gio.DBusConnection) -> None:
        def host_changed(
            _connection: Gio.DBusConnection,
            _sender: str | None,
            _path: str,
            _interface: str,
            _signal: str,
            _parameters: GLib.Variant,
        ) -> None:
            self._query(connection)

        for signal_name in ("StatusNotifierHostRegistered", "StatusNotifierHostUnregistered"):
            self._subscriptions.append(
                connection.signal_subscribe(
                    WATCHER_NAME,
                    WATCHER_INTERFACE,
                    signal_name,
                    WATCHER_PATH,
                    None,
                    Gio.DBusSignalFlags.NONE,
                    host_changed,
                )
            )
        self._watch = Gio.bus_watch_name_on_connection(
            connection,
            WATCHER_NAME,
            Gio.BusNameWatcherFlags.NONE,
            lambda *_args: self._query(connection),
            lambda *_args: self._set(False),
        )

    def _query(self, connection: Gio.DBusConnection) -> None:
        def answered(_source: object, result: Gio.AsyncResult) -> None:
            try:
                (value,) = connection.call_finish(result).unpack()
            except GLib.Error:
                value = False
            self._set(bool(value))

        connection.call(
            WATCHER_NAME,
            WATCHER_PATH,
            "org.freedesktop.DBus.Properties",
            "Get",
            GLib.Variant("(ss)", (WATCHER_INTERFACE, "IsStatusNotifierHostRegistered")),
            GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            answered,
        )

    def _set(self, available: bool) -> None:
        if available != self.available:
            self.available = available
            self._on_change(available)

    def close(self) -> None:
        connection = self._connection.connection if self._connection else None
        if connection is not None:
            for subscription in self._subscriptions:
                connection.signal_unsubscribe(subscription)
            if self._watch:
                Gio.bus_unwatch_name(self._watch)
        self._subscriptions.clear()
        self._watch = 0
        if self._connection:
            self._connection.close()
            self._connection = None


class StatusNotifierItem:
    """The tray icon, shown with show() and taken away with hide(). It registers with
    the desktop's watcher on a connection of its own, again whenever the watcher
    restarts; closing that connection is how the icon goes away."""

    def __init__(
        self,
        item_id: str,
        title: str,
        icon_pixmaps: Sequence[IconPixmap],
        menu: DBusMenu,
        on_activate: Callable[[], object],
        address: str | None,
    ) -> None:
        self._item_id = item_id
        self._title = title
        self._icon_pixmaps = list(icon_pixmaps)
        self._menu = menu
        self._on_activate = on_activate
        self._address = address
        self._connection: _Connection | None = None
        self._registration = 0
        self._watch = 0
        # The desktop may hand over a token that lets the window take focus on Wayland
        self.activation_token = ""

    @property
    def is_visible(self) -> bool:
        return self._connection is not None

    def show(self) -> None:
        if self._connection is None and self._address:
            self._connection = _Connection(self._address, self._opened)

    def hide(self) -> None:
        if self._connection is None:
            return
        connection = self._connection.connection
        if connection is not None:
            if self._watch:
                Gio.bus_unwatch_name(self._watch)
            if self._registration:
                connection.unregister_object(self._registration)
            self._menu.unexport()
        self._watch = 0
        self._registration = 0
        self._connection.close()
        self._connection = None

    def _opened(self, connection: Gio.DBusConnection) -> None:
        self._registration = connection.register_object(
            ITEM_PATH, _NODE_INFO.interfaces[0], self._on_method_call, self._on_get_property, None
        )
        self._menu.export(connection)
        self._watch = Gio.bus_watch_name_on_connection(
            connection,
            WATCHER_NAME,
            Gio.BusNameWatcherFlags.NONE,
            lambda *_args: self._register(connection),
            None,
        )

    def _register(self, connection: Gio.DBusConnection) -> None:
        def registered(_source: object, result: Gio.AsyncResult) -> None:
            try:
                connection.call_finish(result)
            except GLib.Error as error:
                logger.warning("The desktop's tray refused the Qtris icon: %s", error.message)

        # Given only a path, the watcher finds the icon at this connection's name
        connection.call(
            WATCHER_NAME,
            WATCHER_PATH,
            WATCHER_INTERFACE,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (ITEM_PATH,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            registered,
        )

    def _on_method_call(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _interface: str,
        method: str,
        parameters: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        args: Any = parameters.unpack()
        if method == "ProvideXdgActivationToken":
            self.activation_token = args[0]
        elif method in ("Activate", "SecondaryActivate"):
            # Trays that ignore ItemIsMenu send clicks here; showing the window is
            # what each click does there, so repeated clicks agree
            on_activate = self._on_activate

            def activate() -> bool:
                on_activate()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(activate)
        elif method not in ("ContextMenu", "Scroll"):
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
            return
        invocation.return_value(None)

    def _on_get_property(
        self, _connection: Gio.DBusConnection, _sender: str, _path: str, _interface: str, name: str
    ) -> GLib.Variant:
        # GDBus only asks for the properties the interface declares
        no_pixmaps = GLib.Variant("a(iiay)", [])
        return {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", self._item_id),
            "Title": GLib.Variant("s", self._title),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconThemePath": GLib.Variant("s", ""),
            "IconName": GLib.Variant("s", ""),
            "IconPixmap": GLib.Variant("a(iiay)", self._icon_pixmaps),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": no_pixmaps,
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": no_pixmaps,
            "AttentionMovieName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], self._title, "")),
            "ItemIsMenu": GLib.Variant("b", True),
            "Menu": GLib.Variant("o", MENU_PATH),
        }[name]
