"""Expose game sources and store sign-in to Qt Quick."""

from __future__ import annotations

import asyncio
import re
from gettext import gettext as _
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Property, QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtNetwork import QNetworkCookie
from PySide6.QtWebEngineQuick import QQuickWebEngineProfile

from qtris.backend import services, store_login
from qtris.exceptions import QtrisError
from qtris.qt.delegates import PromptController
from qtris.qt.tasks import start_task

if TYPE_CHECKING:
    from collections.abc import Coroutine

# Stores' sign-in pages treat an unknown browser more warily than Chrome itself
_WEB_ENGINE_TOKEN = re.compile(r"\s*QtWebEngine/\S+")


def _web_cookie(cookie: QNetworkCookie) -> store_login.WebCookie:
    expires = None if cookie.isSessionCookie() else cookie.expirationDate().toSecsSinceEpoch()
    return store_login.WebCookie(
        domain=cookie.domain(),
        name=bytes(cookie.name().data()).decode("utf-8", "replace"),
        value=bytes(cookie.value().data()).decode("utf-8", "replace"),
        path=cookie.path() or "/",
        secure=cookie.isSecure(),
        http_only=cookie.isHttpOnly(),
        expires=expires,
    )


class StoreSourcesController(QObject):
    changed = Signal()
    gamesChanged = Signal()
    # A sign-in in the host browser, or in Qtris's own web view
    loginRequested = Signal()
    webLoginRequested = Signal()
    loginClosed = Signal()
    launcherRequested = Signal(str)

    def __init__(self, prompts: PromptController, parent: QObject) -> None:
        super().__init__(parent)
        self._prompts = prompts
        self._source = ""
        self._info: services.SourceStatus | None = None
        self._launchers: tuple[services.SourceLauncher, ...] = ()
        self._request: store_login.BrowserLogin | None = None
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._error = ""
        self._last_epic_code = ""
        # The cookies the sign-in web view has, by domain, path and name
        self._web_cookies: dict[tuple[str, str, str], store_login.WebCookie] = {}
        QGuiApplication.clipboard().dataChanged.connect(self._check_epic_clipboard)
        app = QGuiApplication.instance()
        if isinstance(app, QGuiApplication):
            app.applicationStateChanged.connect(self._check_epic_clipboard)

    @Property(dict, notify=changed)
    def source(self) -> dict[str, str | bool]:
        info = self._info
        if not info:
            return {}
        return {
            "name": info.name,
            "online": info.online,
            "signedIn": info.signed_in,
            "canSignIn": info.can_sign_in,
        }

    @Property(dict, notify=changed)
    def login(self) -> dict[str, str]:
        request = self._request
        if not request:
            return {}
        login = {"name": request.name, "inputKind": request.input_kind}
        if request.input_kind == "web":
            login["url"] = request.url
        return login

    @Property(list, notify=changed)
    def launchers(self) -> list[dict[str, str]]:
        return [
            {
                "id": launcher.id,
                "label": f"{launcher.name} — {launcher.directory}" if launcher.directory else launcher.name,
            }
            for launcher in self._launchers
        ]

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._task is not None

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    def select(self, service_id: str) -> None:
        self._source = service_id
        self._info = None
        self._launchers = ()
        self.changed.emit()
        if service_id:
            start_task(self._load(service_id), self._prompts.error)

    async def _load(self, service_id: str) -> None:
        info = await services.get_source_status(service_id)
        launchers = await services.list_source_launchers(service_id)
        if service_id == self._source:
            self._info = info
            self._launchers = launchers
            self.changed.emit()

    @Slot(str)
    def choose_launcher(self, game_id: str) -> None:
        if self._source and not self._task:
            self._start(self._choose_launcher(self._source, game_id))

    async def _choose_launcher(self, service_id: str, game_id: str) -> None:
        await services.set_source_launcher(service_id, game_id)
        await self._load(service_id)

    @Slot()
    def run_launcher(self) -> None:
        if self._launchers and not self._task:
            self.launcherRequested.emit(self._launchers[0].id)

    @Slot()
    def sign_in(self) -> None:
        if self._info and self._info.can_sign_in and not self._info.signed_in and not self._request and not self._task:
            self._start(self._begin(self._source))

    async def _begin(self, service_id: str) -> None:
        request = await store_login.begin(service_id)
        if self._closed or self._source != service_id:
            await store_login.cancel(request.id)
            return
        self._request = request
        self._error = ""
        self._web_cookies.clear()
        if request.input_kind == "web":
            self.webLoginRequested.emit()
        else:
            self.loginRequested.emit()
        if request.input_kind == "epic_code":
            # A response already on the clipboard may belong to an earlier sign-in
            self._last_epic_code = store_login.epic_authorization_code(QGuiApplication.clipboard().text())
            self.open_browser()

    @Slot()
    def _check_epic_clipboard(self) -> None:
        if self._closed or not self._request or self._request.input_kind != "epic_code" or self._task:
            return
        if QGuiApplication.applicationState() != Qt.ApplicationState.ApplicationActive:
            return
        code = store_login.epic_authorization_code(QGuiApplication.clipboard().text())
        if code and code != self._last_epic_code:
            self.submit(code)

    @Slot(QQuickWebEngineProfile)
    def attach_web_profile(self, profile: QQuickWebEngineProfile) -> None:
        """Take the sign-in web view's private profile: present it as plain Chrome
        and keep the cookies it gets, which some stores need besides the token."""
        profile.setHttpUserAgent(_WEB_ENGINE_TOKEN.sub("", profile.httpUserAgent()))
        store = profile.cookieStore()
        store.cookieAdded.connect(self._add_web_cookie)
        store.cookieRemoved.connect(self._remove_web_cookie)

    def _add_web_cookie(self, cookie: QNetworkCookie) -> None:
        web_cookie = _web_cookie(cookie)
        self._web_cookies[(web_cookie.domain, web_cookie.path, web_cookie.name)] = web_cookie

    def _remove_web_cookie(self, cookie: QNetworkCookie) -> None:
        web_cookie = _web_cookie(cookie)
        self._web_cookies.pop((web_cookie.domain, web_cookie.path, web_cookie.name), None)

    @Slot(QUrl)
    def web_url_changed(self, url: QUrl) -> None:
        """Finish the sign-in when the web view reaches an address that is the response."""
        address = url.toString(QUrl.ComponentFormattingOption.FullyEncoded)
        if self._request and not self._task and store_login.is_web_response(self._request.id, address):
            self._submit_web(address)

    def _submit_web(self, value: str) -> None:
        if self._request and not self._task:
            cookies = tuple(self._web_cookies.values())
            self._start(self._submit(self._source, self._request.id, value, cookies))

    @Slot()
    def clear_error(self) -> None:
        if self._error:
            self._error = ""
            self.changed.emit()

    @Slot()
    def open_browser(self) -> None:
        if self._request and not QDesktopServices.openUrl(QUrl(self._request.url)):
            self._error = _("Could not open your default browser.")
            self.changed.emit()

    @Slot(str)
    def submit(self, text: str) -> None:
        if self._request and not self._task:
            if self._request.input_kind == "epic_code":
                code = store_login.epic_authorization_code(text, allow_plain=True)
                if code:
                    self._last_epic_code = code
            self._start(self._submit(self._source, self._request.id, text))

    @Slot(QUrl)
    def submit_cookie_file(self, url: QUrl) -> None:
        if self._request and self._request.input_kind == "cookies" and url.isLocalFile():
            self.submit(url.toLocalFile())

    async def _submit(
        self, service_id: str, request_id: str, text: str, cookies: tuple[store_login.WebCookie, ...] = ()
    ) -> None:
        self._error = ""
        try:
            await store_login.finish(request_id, text, cookies)
        except QtrisError as error:
            self._error = error.message
            return
        self._request = None
        self._last_epic_code = ""
        self._web_cookies.clear()
        self.loginClosed.emit()
        await self._reload(service_id)

    def shutdown(self) -> None:
        self._closed = True
        self.close_login()

    @Slot()
    def close_login(self) -> None:
        if self._request and not self._task:
            request, self._request = self._request, None
            self._last_epic_code = ""
            self._web_cookies.clear()
            start_task(store_login.cancel(request.id), self._prompts.error)
            self.changed.emit()

    @Slot()
    def sync(self) -> None:
        if self._info and (not self._info.online or self._info.signed_in) and not self._task:
            self._start(self._reload(self._source))

    async def _reload(self, service_id: str) -> None:
        try:
            await services.sync_source(service_id)
        finally:
            await self._load(service_id)
            self.gamesChanged.emit()

    @Slot()
    def disconnect_store(self) -> None:
        if self._info and self._info.signed_in and not self._task:
            self._start(self._disconnect(self._source))

    async def _disconnect(self, service_id: str) -> None:
        await services.disconnect_source(service_id)
        await self._load(service_id)
        self.gamesChanged.emit()

    def _start(self, operation: Coroutine[Any, Any, None]) -> None:
        self._task = start_task(self._run(operation), self._prompts.error)
        self.changed.emit()

    async def _run(self, operation: Coroutine[Any, Any, None]) -> None:
        try:
            await operation
        finally:
            self._task = None
            self.changed.emit()
            self._check_epic_clipboard()
