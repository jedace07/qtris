"""Each web game's own browser profile."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PySide6.QtWebEngineCore import (
    QWebEngineProfile,
    QWebEngineProfileBuilder,
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)

from qtris.qt.web_player.local_server import HOST, TOKEN_HEADER

if TYPE_CHECKING:
    from pathlib import Path

    from PySide6.QtCore import QObject

# Sites treat an unknown browser more warily than Chrome itself
_WEB_ENGINE_TOKEN = re.compile(r"\s*QtWebEngine/\S+")


def create_profile(directory: Path, cache: Path, user_agent: str = "") -> QWebEngineProfile:
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    builder = QWebEngineProfileBuilder()
    # The paths must be set first, or Chromium starts in a folder every game would share
    builder.setPersistentStoragePath(str(directory / "storage"))
    builder.setCachePath(str(cache))
    profile = builder.createProfile("game")
    profile.setHttpUserAgent(user_agent or _WEB_ENGINE_TOKEN.sub("", profile.httpUserAgent()))

    settings = profile.settings()
    attribute = QWebEngineSettings.WebAttribute
    settings.setAttribute(attribute.FullScreenSupportEnabled, True)
    settings.setAttribute(attribute.PlaybackRequiresUserGesture, False)
    # Games copy save codes and room links; reading the clipboard stays blocked
    settings.setAttribute(attribute.JavascriptCanAccessClipboard, True)
    # Pop-ups need a click
    settings.setAttribute(attribute.JavascriptCanOpenWindows, False)
    settings.setAttribute(attribute.LocalContentCanAccessFileUrls, False)
    # A page mustn't start programs through schemes like steam:
    settings.setUnknownUrlSchemePolicy(QWebEngineSettings.UnknownUrlSchemePolicy.DisallowUnknownUrlSchemes)
    return profile


class LocalGameInterceptor(QWebEngineUrlRequestInterceptor):
    """Add the local server's token to the game's requests to it."""

    def __init__(self, token: str, parent: QObject) -> None:
        super().__init__(parent)
        self._token = token.encode()

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:  # noqa: N802
        url = info.requestUrl()
        if url.scheme() == "http" and url.host() == HOST and url.port(80) == 80:
            info.setHttpHeader(TOKEN_HEADER.encode(), self._token)
