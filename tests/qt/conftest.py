"""Run the Qt Quick frontend with the offscreen platform and software renderer."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
# A stale compiled QML cache makes the loader thread translate an error message. The Python
# translator then waits for the GIL that a test holds inside a synchronous QTest call
os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWebEngineQuick import QtWebEngineQuick  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from qtris.qt.translations import QmlTranslator  # noqa: E402


@pytest.fixture(scope="session")
def qt_application() -> QGuiApplication:
    application = QGuiApplication.instance()
    assert application is None or isinstance(application, QGuiApplication)
    if application is None:
        # The stores' sign-in web view needs its setup before the application exists
        QtWebEngineQuick.initialize()
        application = QApplication([])
    application.installTranslator(QmlTranslator(application))
    return application
