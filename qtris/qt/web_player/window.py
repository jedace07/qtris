"""A window showing a game's page, and the pop-ups it opens."""

from __future__ import annotations

import html
import logging
from gettext import gettext as _

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWebEngineCore import (
    QWebEngineFullScreenRequest,
    QWebEngineNewWindowRequest,
    QWebEnginePage,
    QWebEnginePermission,
    QWebEngineProfile,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from qtris.qt.web_player.external_links import open_in_browser

log = logging.getLogger(__name__)

# Chromium needs a moment after a page unloads to commit what it saved on the way out
_COMMIT_MS = 500
# Qtris kills a game five seconds after asking it to stop, so leave well before that
_UNLOAD_LIMIT_MS = 2000


class GamePage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, parent: WebWindow) -> None:
        super().__init__(profile, parent)
        self.unloading = False

    def acceptNavigationRequest(  # noqa: N802
        self, url: QUrl | str, type: QWebEnginePage.NavigationType, isMainFrame: bool
    ) -> bool:
        # Reached by dropping a file on the window, for one, and a file: page reads any file
        return QUrl(url).scheme() != "file"

    def javaScriptConfirm(self, securityOrigin: QUrl | str, msg: str) -> bool:  # noqa: N802
        # Asking whether to leave would hold up closing or stopping the game
        return self.unloading or super().javaScriptConfirm(securityOrigin, msg)


class WebWindow(QWebEngineView):
    """One browser window. Closing it unloads the page first, so the game can save."""

    unloaded = Signal()

    def __init__(self, profile: QWebEngineProfile, *, links_in_game: bool, parent: WebWindow | None = None) -> None:
        super().__init__(parent)
        if parent:
            self.setWindowFlag(Qt.WindowType.Window)
        self._profile = profile
        self._links_in_game = links_in_game
        self._windowed_state = Qt.WindowState.WindowNoState
        self._state_before_page_fullscreen = Qt.WindowState.WindowNoState
        self._page_fullscreen = False
        self.game_page = GamePage(profile, self)
        self.setPage(self.game_page)

        self._unload_timer = QTimer(self, singleShot=True)
        self._unload_timer.timeout.connect(self.unloaded)

        self.game_page.permissionRequested.connect(self._answer_permission)
        self.game_page.fullScreenRequested.connect(self._set_page_fullscreen)
        self.game_page.newWindowRequested.connect(self._open_window)
        self.game_page.windowCloseRequested.connect(self.close)
        self.game_page.renderProcessTerminated.connect(self._show_crash)
        self.game_page.loadFinished.connect(self._page_loaded)

        QShortcut(QKeySequence(Qt.Key.Key_F11), self).activated.connect(self.toggle_fullscreen)
        # QtWebEngine leaves the way out of a page's fullscreen to the application
        self._escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._escape.setEnabled(False)
        self._escape.activated.connect(self._exit_page_fullscreen)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        event.ignore()
        self.unload()

    def unload(self) -> None:
        """Leave the page, so its exit handlers run, then emit unloaded."""
        if self.game_page.unloading:
            return
        self.game_page.unloading = True
        for popup in self.findChildren(WebWindow):
            popup.unload()
        self.hide()
        self._unload_timer.start(_UNLOAD_LIMIT_MS)
        self.game_page.load(QUrl("about:blank"))

    def _page_loaded(self, _succeeded: bool) -> None:
        if self.game_page.unloading and self._unload_timer.remainingTime() > _COMMIT_MS:
            self._unload_timer.start(_COMMIT_MS)

    def toggle_fullscreen(self) -> None:
        if self._page_fullscreen:
            self._exit_page_fullscreen()
        elif self.isFullScreen():
            self.setWindowState(self._windowed_state)
        else:
            self._windowed_state = self.windowState()
            self._enter_fullscreen()

    def _enter_fullscreen(self) -> None:
        # Not showFullScreen, which drops the maximized state, and Wayland can't restore that
        self.setWindowState(self.windowState() | Qt.WindowState.WindowFullScreen)

    def _exit_page_fullscreen(self) -> None:
        self.triggerPageAction(QWebEnginePage.WebAction.ExitFullScreen)

    def _set_page_fullscreen(self, request: QWebEngineFullScreenRequest) -> None:
        request.accept()
        fullscreen = request.toggleOn()
        if fullscreen == self._page_fullscreen:
            return
        self._page_fullscreen = fullscreen
        self._escape.setEnabled(fullscreen)
        if fullscreen:
            self._state_before_page_fullscreen = self.windowState()
            self._enter_fullscreen()
        else:
            self.setWindowState(self._state_before_page_fullscreen)

    def _answer_permission(self, permission: QWebEnginePermission) -> None:
        if permission.permissionType() == QWebEnginePermission.PermissionType.MouseLock:
            permission.grant()
        else:
            log.info("Denied %s to %s", permission.permissionType().name, permission.origin().host())
            permission.deny()

    def _open_window(self, request: QWebEngineNewWindowRequest) -> None:
        if self.game_page.unloading:
            return
        url = request.requestedUrl()
        scheme = url.scheme()
        if scheme in ("http", "https"):
            dialog = request.destination() == QWebEngineNewWindowRequest.DestinationType.InNewDialog
            same_site = url.host() == self.game_page.url().host()
            # Sign-in pop-ups and the game's own windows need the game's profile
            if not (dialog or same_site or self._links_in_game):
                if request.isUserInitiated():
                    open_in_browser(url)
                return
        elif scheme not in ("", "about", "blob"):
            log.info("Blocked a window for a %s: address", scheme)
            return

        popup = WebWindow(self._profile, links_in_game=self._links_in_game, parent=self)
        popup.setWindowTitle(self.windowTitle())
        popup.game_page.titleChanged.connect(popup.setWindowTitle)
        popup.unloaded.connect(popup.deleteLater)
        geometry = request.requestedGeometry()
        popup.resize(geometry.size() if geometry.width() > 0 and geometry.height() > 0 else self.size())
        request.openIn(popup.game_page)
        popup.show()

    def _show_crash(self, status: QWebEnginePage.RenderProcessTerminationStatus, exit_code: int) -> None:
        if status == QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus or self.game_page.unloading:
            return
        log.error("The game's page stopped (%s, exit code %s)", status.name, exit_code)
        address = html.escape(self.game_page.url().toString())
        message = html.escape(_("The game's page crashed."))
        reload = html.escape(_("Reload"))
        page = f'<meta charset="utf-8"><p>{message} <a href="{address}">{reload}</a></p>'
        # The dead renderer can't take a new page until this signal returns
        QTimer.singleShot(0, lambda: self.game_page.setHtml(page))
