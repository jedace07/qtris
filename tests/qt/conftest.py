"""Run the Qt Quick frontend with the offscreen platform and software renderer."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWebEngineQuick import QtWebEngineQuick  # noqa: E402

from qtris.qt.translations import QmlTranslator  # noqa: E402


@pytest.fixture(scope="session")
def qt_application() -> QGuiApplication:
    application = QGuiApplication.instance()
    assert application is None or isinstance(application, QGuiApplication)
    if application is None:
        # The stores' sign-in web view needs its setup before the application exists
        QtWebEngineQuick.initialize()
        application = QGuiApplication([])
    application.installTranslator(QmlTranslator(application))
    return application
