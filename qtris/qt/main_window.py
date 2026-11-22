"""Load and own the Qt Quick library window."""

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle

from qtris.qt.delegates import PromptController
from qtris.qt.library_controller import LibraryController

QML_DIR = Path(__file__).with_name("qml")
# The Qt Quick Controls style in QML_DIR that every control in the window uses
STYLE = "QtrisStyle"


class MainWindow(QObject):
    def __init__(self) -> None:
        super().__init__()
        # The style applies to the whole process, and must be chosen before any control loads
        if QQuickStyle.name() != STYLE:
            QQuickStyle.setStyle(STYLE)
        self.engine = QQmlApplicationEngine(self)
        self.engine.addImportPath(str(QML_DIR))
        self.prompts = PromptController()
        self.prompts.setParent(self)
        self.controller = LibraryController(self.prompts)
        self.controller.setParent(self)
        self.engine.setInitialProperties({"controller": self.controller, "prompts": self.prompts})
        self.engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
        root = next(iter(self.engine.rootObjects()), None)
        if not isinstance(root, QQuickWindow):
            self.controller.shutdown()
            raise RuntimeError("Could not load the Qtris QML window")
        self.window = root
        self.window.visibleChanged.connect(lambda: self.prompts.set_interactive(self.window.isVisible()))
        self.load_task = self.controller.start()

    def show(self) -> None:
        self.restore()
        self.window.setVisible(True)
        self.window.requestActivate()

    def minimize(self) -> None:
        self.window.setWindowStates(self.window.windowStates() | Qt.WindowState.WindowMinimized)

    def restore(self) -> None:
        self.window.setWindowStates(self.window.windowStates() & ~Qt.WindowState.WindowMinimized)

    def shutdown(self) -> None:
        self.prompts.set_interactive(False)
        self.controller.shutdown()
        self.prompts.dismiss()
        self.window.close()
