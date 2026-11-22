"""Open a game's outside links in the user's browser."""

from __future__ import annotations

import logging

from gi.repository import Gio, GLib
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

log = logging.getLogger(__name__)


def open_in_browser(url: QUrl) -> None:
    """Ask the desktop portal to open a link.

    A browser started from this process would be part of the game's process tree, so the
    game would run until the browser quit, and stopping the game would close the browser.
    """
    address = bytes(url.toEncoded().data()).decode()

    def finished(bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        try:
            bus.call_finish(result)
        except GLib.Error as error:
            log.warning("The desktop portal couldn't open the link: %s", error.message)
            QDesktopServices.openUrl(QUrl(address))

    try:
        Gio.bus_get_sync(Gio.BusType.SESSION).call(
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.OpenURI",
            "OpenURI",
            GLib.Variant("(ssa{sv})", ("", address, {})),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            finished,
        )
    except GLib.Error as error:
        log.warning("There is no session bus to open the link with: %s", error.message)
        QDesktopServices.openUrl(QUrl(address))
