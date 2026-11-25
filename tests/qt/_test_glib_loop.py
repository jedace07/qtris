"""Qt hears that GLib's loop is about to wait, as with Qt's own loop."""

from __future__ import annotations

import pytest
from gi.repository import GLib
from PySide6.QtCore import QAbstractEventDispatcher
from PySide6.QtGui import QGuiApplication

from qtris.qt.glib_loop import notify_qt_before_waiting


# Iterating imports PyGObject's asyncio integration, as run_asyncio_on_glib() does quietly
@pytest.mark.filterwarnings("ignore::DeprecationWarning:gi.events")
def test_glib_iterations_emit_about_to_block(qt_application: QGuiApplication) -> None:
    assert qt_application
    context = GLib.MainContext()
    dispatcher = QAbstractEventDispatcher.instance()
    assert dispatcher is not None
    emitted: list[bool] = []
    dispatcher.aboutToBlock.connect(lambda: emitted.append(True))
    source = notify_qt_before_waiting(context)
    try:
        context.iteration(False)
        assert emitted
    finally:
        source.destroy()
    emitted.clear()
    context.iteration(False)
    assert not emitted
