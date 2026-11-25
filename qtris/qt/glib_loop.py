"""Run Qt on GLib's main loop as Qt's own loop would.

Gio.Application.run() runs GLib's loop, and Qt dispatches its events through it, but
Qt's own loop also emits aboutToBlock before it waits for events. Qt's Wayland backend
sends its requests to the compositor then, so without it they wait until something
else sends them. A window shown again after being hidden then never appeared, since a
hidden window draws nothing that would send them.
"""

from __future__ import annotations

from gi.repository import GLib
from PySide6.QtCore import QAbstractEventDispatcher


class _AboutToBlockSource(GLib.Source):
    """Emits the event dispatcher's aboutToBlock before each wait of GLib's loop."""

    def __init__(self, dispatcher: QAbstractEventDispatcher) -> None:
        super().__init__()
        self._dispatcher = dispatcher

    def prepare(self) -> tuple[bool, int]:
        self._dispatcher.aboutToBlock.emit()
        return False, -1

    def check(self) -> bool:
        return False

    def dispatch(self, _callback: object, _args: object) -> bool:
        return GLib.SOURCE_CONTINUE


def notify_qt_before_waiting(context: GLib.MainContext | None = None) -> GLib.Source:
    """Have GLib's loop on 'context' (by default the default one, which
    Gio.Application.run() runs) tell Qt before it waits, once Qt's application exists;
    returns the source, which destroying undoes."""
    dispatcher = QAbstractEventDispatcher.instance()
    if dispatcher is None:
        raise RuntimeError("Qt's application must exist before its loop runs on GLib's")
    source = _AboutToBlockSource(dispatcher)
    source.set_name("Qt aboutToBlock")
    source.attach(context)
    return source
