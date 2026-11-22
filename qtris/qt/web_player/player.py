"""Run one web game until its window closes or Qtris stops it."""

from __future__ import annotations

import logging
import os
import signal
import socket
import sys
from contextlib import contextmanager
from gettext import gettext as _
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QSize, QSocketNotifier, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEngineProfile
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from shiboken6 import delete

from qtris import settings
from qtris.qt.web_player.local_server import LocalGameServer
from qtris.qt.web_player.profile import LocalGameInterceptor, create_profile, lock_profile
from qtris.qt.web_player.window import WebWindow

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from qtris.qt.web_player.options import PlayerOptions

log = logging.getLogger(__name__)

_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def run(options: PlayerOptions) -> int:
    url, server = options.url, None
    address = urlsplit(url)
    if address.scheme == "file":
        entry = Path(unquote(address.path))
        server = LocalGameServer(entry.parent, shared_memory=options.shared_memory)
        url = server.get_url(entry, address.query, address.fragment)
        server.start()

        # In the variable rather than the arguments, which Chromium ignores if it's set
        flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f'{flags} "{server.resolver_rule}"'.strip()

    # Without the game's options, so nothing in its configuration becomes a Chromium switch
    app = QApplication([sys.argv[0]])
    app.setApplicationName("qtris-web-player")
    app.setDesktopFileName(settings.APPLICATION_ID)
    app.setWindowIcon(QIcon(options.icon))
    app.setQuitOnLastWindowClosed(False)

    with lock_profile(options.profile_dir) as locked:
        if not locked:
            log.error("Another player is using %s", options.profile_dir)
            QMessageBox.critical(None, options.name, _("This game is already running."))
            return 1
        profile = create_profile(options.profile_dir, options.cache_dir, options.user_agent)
        if server:
            # Held in a variable while the game runs, as neither its parent nor the profile keeps
            # its Python side alive, and without that no request gets the token
            interceptor = LocalGameInterceptor(server.token, profile)
            profile.setUrlRequestInterceptor(interceptor)
        return _play(app, profile, options, url)


def _play(app: QApplication, profile: QWebEngineProfile, options: PlayerOptions, url: str) -> int:
    window = WebWindow(profile, links_in_game=options.links_in_game)
    window.setWindowTitle(options.name)
    # Not QApplication.quit, which would ask the closing window and be refused
    window.unloaded.connect(lambda: app.exit(0))

    def save_download(download: QWebEngineDownloadRequest) -> None:
        # Games export saves as downloads
        suggested = Path(download.downloadDirectory()) / download.downloadFileName()
        parent = app.activeWindow() or window
        path, _filter = QFileDialog.getSaveFileName(parent, _("Save file"), str(suggested))
        if path:
            download.setDownloadDirectory(str(Path(path).parent))
            download.setDownloadFileName(Path(path).name)
            download.accept()

    profile.downloadRequested.connect(save_download)

    size = QSize(options.width, options.height)
    if screen := app.primaryScreen():
        size = size.boundedTo(screen.availableGeometry().size())
    window.resize(size)
    if options.window_mode == "fullscreen":
        window.showFullScreen()
    elif options.window_mode == "maximized":
        window.showMaximized()
    else:
        window.show()
    window.setFocus()
    window.load(QUrl(url))

    with _handle_stop_signals(window.unload):
        status = app.exec()
        # The pages have to go before their profile, which writes its data as it's deleted
        delete(window)
        delete(profile)
    return status


@contextmanager
def _handle_stop_signals(stop: Callable[[], None]) -> Generator[None]:
    """Turn termination signals into a normal close.

    Dying by one loses what the game saved in the last few seconds. Further signals are
    ignored, as Qtris's stop and the wrapper's can both arrive.
    """
    receiver, sender = socket.socketpair()
    receiver.setblocking(False)
    sender.setblocking(False)
    notifier = QSocketNotifier(receiver.fileno(), QSocketNotifier.Type.Read)

    def wake() -> None:
        try:
            received = receiver.recv(64)
        except BlockingIOError:
            return
        # The socket also hears of other handled signals, such as SIGCHLD
        if any(number in _STOP_SIGNALS for number in received):
            stop()

    notifier.activated.connect(wake)
    signal.set_wakeup_fd(sender.fileno(), warn_on_full_buffer=False)
    for number in _STOP_SIGNALS:
        signal.signal(number, lambda *_arguments: None)
    try:
        yield
    finally:
        for number in _STOP_SIGNALS:
            signal.signal(number, signal.SIG_IGN)
        notifier.setEnabled(False)
        signal.set_wakeup_fd(-1)
        receiver.close()
        sender.close()
