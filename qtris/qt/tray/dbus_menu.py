"""Export the tray icon's menu over D-Bus with the com.canonical.dbusmenu protocol,
which the desktop's tray shows when the icon is clicked."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from gi.repository import Gio, GLib

from qtris.util.log import logger

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

MENU_PATH = "/MenuBar"
INTERFACE = "com.canonical.dbusmenu"
ROOT_ID = 0

_NODE_INFO = Gio.DBusNodeInfo.new_for_xml(
    f"""<node>
  <interface name="{INTERFACE}">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{{sv}}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{{sv}})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{{sv}})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg type="i" name="id"/>
      <arg type="u" name="timestamp"/>
    </signal>
  </interface>
</node>"""
)

_item_ids = itertools.count(ROOT_ID + 1)


@dataclass(eq=False)
class MenuItem:
    """An entry of the menu. Its id stays the same while it's in the menu, so a click
    on an entry the desktop showed before the menu changed can't reach another one."""

    label: str = ""
    on_click: Callable[[], object] | None = None
    separator: bool = False
    visible: bool = True
    id: int = field(default_factory=lambda: next(_item_ids), init=False)

    def properties(self) -> dict[str, GLib.Variant]:
        if self.separator:
            properties = {"type": GLib.Variant("s", "separator")}
        else:
            # Underscores mark access keys, so doubling them keeps them in the text
            properties = {"label": GLib.Variant("s", self.label.replace("_", "__"))}
        if not self.visible:
            properties["visible"] = GLib.Variant("b", False)
        return properties


def _select(properties: dict[str, GLib.Variant], names: Sequence[str]) -> dict[str, GLib.Variant]:
    return {name: value for name, value in properties.items() if not names or name in names}


class DBusMenu:
    """A flat menu of MenuItems, exported on a D-Bus connection at MENU_PATH."""

    def __init__(self) -> None:
        self._items: list[MenuItem] = []
        self._revision = 1
        self._connection: Gio.DBusConnection | None = None
        self._registration = 0

    @property
    def items(self) -> tuple[MenuItem, ...]:
        return tuple(self._items)

    def set_items(self, items: Iterable[MenuItem]) -> None:
        self._items = list(items)
        self.changed()

    def changed(self) -> None:
        """Tell the desktop to read the menu again after an item changed."""
        self._revision += 1
        if self._connection is not None and not self._connection.is_closed():
            self._connection.emit_signal(
                None, MENU_PATH, INTERFACE, "LayoutUpdated", GLib.Variant("(ui)", (self._revision, ROOT_ID))
            )

    def export(self, connection: Gio.DBusConnection) -> None:
        self.unexport()
        self._registration = connection.register_object(
            MENU_PATH, _NODE_INFO.interfaces[0], self._on_method_call, self._on_get_property, None
        )
        self._connection = connection

    def unexport(self) -> None:
        if self._connection is not None and self._registration:
            self._connection.unregister_object(self._registration)
        self._connection = None
        self._registration = 0

    def _find(self, item_id: int) -> MenuItem | None:
        return next((item for item in self._items if item.id == item_id), None)

    def _root_properties(self) -> dict[str, GLib.Variant]:
        return {"children-display": GLib.Variant("s", "submenu")}

    def _layout(self, parent_id: int, names: Sequence[str]) -> GLib.Variant | None:
        if parent_id == ROOT_ID:
            children = [
                GLib.Variant("(ia{sv}av)", (item.id, _select(item.properties(), names), [])) for item in self._items
            ]
            return GLib.Variant("(ia{sv}av)", (ROOT_ID, _select(self._root_properties(), names), children))
        if item := self._find(parent_id):
            return GLib.Variant("(ia{sv}av)", (item.id, _select(item.properties(), names), []))
        return None

    def _properties_of(self, item_id: int) -> dict[str, GLib.Variant] | None:
        if item_id == ROOT_ID:
            return self._root_properties()
        item = self._find(item_id)
        return item.properties() if item else None

    def _handle_event(self, item_id: int, event_id: str) -> bool:
        item = self._find(item_id)
        if item is None:
            return False
        if event_id == "clicked" and item.on_click and item.visible:
            on_click = item.on_click

            def click() -> bool:
                on_click()
                return GLib.SOURCE_REMOVE

            # Reply to the desktop before acting, since quitting closes the connection
            GLib.idle_add(click)
        return True

    def _on_method_call(  # noqa: C901
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
        try:
            if method == "GetLayout":
                parent_id, _depth, names = args
                layout = self._layout(parent_id, names)
                if layout is None:
                    raise LookupError(parent_id)
                invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("u", self._revision), layout))
            elif method == "GetGroupProperties":
                ids, names = args
                found = [
                    (item_id, _select(properties, names))
                    for item_id in ids or [ROOT_ID, *(item.id for item in self._items)]
                    if (properties := self._properties_of(item_id)) is not None
                ]
                invocation.return_value(GLib.Variant("(a(ia{sv}))", (found,)))
            elif method == "GetProperty":
                item_id, name = args
                properties = self._properties_of(item_id)
                if properties is None or name not in properties:
                    raise LookupError(item_id)
                invocation.return_value(GLib.Variant("(v)", (properties[name],)))
            elif method == "Event":
                item_id, event_id, _data, _timestamp = args
                if not self._handle_event(item_id, event_id):
                    raise LookupError(item_id)
                invocation.return_value(None)
            elif method == "EventGroup":
                errors = [
                    item_id
                    for item_id, event_id, _data, _timestamp in args[0]
                    if not self._handle_event(item_id, event_id)
                ]
                invocation.return_value(GLib.Variant("(ai)", (errors,)))
            elif method == "AboutToShow":
                invocation.return_value(GLib.Variant("(b)", (False,)))
            elif method == "AboutToShowGroup":
                errors = [item_id for item_id in args[0] if self._properties_of(item_id) is None]
                invocation.return_value(GLib.Variant("(aiai)", ([], errors)))
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except LookupError as error:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.InvalidArgs", f"No menu item {error}")
        except Exception as error:
            logger.exception("Tray menu call %s failed: %s", method, error)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(error))

    def _on_get_property(
        self, _connection: Gio.DBusConnection, _sender: str, _path: str, _interface: str, name: str
    ) -> GLib.Variant:
        # GDBus only asks for the properties the interface declares
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }[name]
