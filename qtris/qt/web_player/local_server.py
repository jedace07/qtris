"""Serve a local HTML game's folder to its player, and to nothing else.

A file: page can read every file the user can, and a custom URL scheme can't give
responses a working error body, a content encoding or cross-origin isolation. So the
folder is served over loopback HTTP on a random port, and Chromium resolves a fixed host
name to that port: the game's origin, which its saves belong to, is the same each launch.
Only requests with the token that the player adds are answered.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING, cast
from urllib.parse import quote, unquote, urlsplit

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

HOST = "game.qtris.localhost"
TOKEN_HEADER = "X-Qtris-Player"

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
# The compressed files Unity's web builds expect a server to declare. Other .gz files
# are left alone, as games that fetch those unpack them themselves
_PRECOMPRESSED = re.compile(r"(.+\.(?:data|wasm|js|symbols\.json))\.(gz|br)")
_ENCODINGS = {"gz": "gzip", "br": "br"}
# The host's MIME database varies, and WebAssembly and modules insist on these
_CONTENT_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".wasm": "application/wasm"}


def get_content_type(path: str) -> str:
    if known := _CONTENT_TYPES.get(os.path.splitext(path)[1].lower()):
        return known
    guessed, encoding = mimetypes.guess_file_type(path)
    return guessed if guessed and not encoding else "application/octet-stream"


def parse_range(header: str, size: int) -> range | None:
    """Return the bytes a Range header asks for, empty if it can't be met, or None for all of them."""
    match = _RANGE.fullmatch(header.strip())
    if not match or not any(match.groups()):
        return None
    first, last = match.groups()
    if not first:
        return range(max(0, size - int(last)), size)
    if last and int(last) < int(first):
        return None
    return range(int(first), min(int(last) + 1, size) if last else size)


class LocalGameServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, root: Path, *, shared_memory: bool = False) -> None:
        self.root = root.resolve()
        self.shared_memory = shared_memory
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", 0), _Handler)

    @property
    def resolver_rule(self) -> str:
        """The Chromium switch that points the game's host at this server."""
        return f"--host-resolver-rules=MAP {HOST}:80 127.0.0.1:{self.server_port}"

    def get_url(self, entry: Path, query: str = "", fragment: str = "") -> str:
        url = f"http://{HOST}/{quote(entry.resolve().relative_to(self.root).as_posix())}"
        if query:
            url += f"?{query}"
        return f"{url}#{fragment}" if fragment else url

    def find_path(self, url_path: str) -> Path | None:
        """Return what a request's path names inside the game's folder, leaving out hidden files."""
        parts = [part for part in unquote(url_path).split("/") if part]
        if any(part.startswith(".") for part in parts):
            return None
        try:
            path = self.root.joinpath(*parts).resolve(strict=True)
        except OSError, ValueError:
            return None
        # A link could lead out of the folder, or to a hidden file in it
        if not path.is_relative_to(self.root):
            return None
        if any(part.startswith(".") for part in path.relative_to(self.root).parts):
            return None
        return path

    def start(self) -> None:
        Thread(target=self.serve_forever, name="web-game-server", daemon=True).start()

    def handle_error(self, request: object, client_address: object) -> None:
        log.debug("A request for a game file ended early", exc_info=True)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True

    @property
    def game(self) -> LocalGameServer:
        return cast("LocalGameServer", self.server)

    def log_message(self, format: str, *args: object) -> None:
        log.debug(format, *args)

    def do_GET(self) -> None:
        self._respond(body=True)

    def do_HEAD(self) -> None:
        self._respond(body=False)

    def _respond(self, *, body: bool) -> None:
        token = self.headers.get(TOKEN_HEADER, "")
        if not secrets.compare_digest(token.encode(), self.game.token.encode()):
            self._send_empty(HTTPStatus.FORBIDDEN)
            return
        try:
            target = urlsplit(self.path)
        except ValueError:
            self._send_empty(HTTPStatus.BAD_REQUEST)
            return
        path = self.game.find_path(target.path)
        if path and path.is_dir():
            if not target.path.endswith("/"):
                # Built from the resolved path, so it can't name another host
                location = quote(f"/{path.relative_to(self.game.root).as_posix()}/")
                if target.query:
                    location += f"?{target.query}"
                self._send_empty(HTTPStatus.MOVED_PERMANENTLY, {"Location": location})
                return
            path = self.game.find_path(f"{target.path}index.html")
        if not path or not path.is_file():
            if target.path != "/favicon.ico":
                log.info("The game asked for %s, which isn't in its folder", target.path)
            self._send_empty(HTTPStatus.NOT_FOUND)
            return
        try:
            self._send_file(path, body=body)
        except PermissionError:
            self._send_empty(HTTPStatus.FORBIDDEN)

    def _send_file(self, path: Path, *, body: bool) -> None:
        with path.open("rb") as file:
            size = os.fstat(file.fileno()).st_size
            content, status = range(size), HTTPStatus.OK
            headers = {"Accept-Ranges": "bytes"}
            name = path.name
            if match := _PRECOMPRESSED.fullmatch(name):
                name = match[1]
                headers["Content-Encoding"] = _ENCODINGS[match[2]]
            elif requested := self.headers.get("Range"):
                if (part := parse_range(requested, size)) is not None:
                    if not part:
                        self._send_empty(HTTPStatus.RANGE_NOT_SATISFIABLE, {"Content-Range": f"bytes */{size}"})
                        return
                    content, status = part, HTTPStatus.PARTIAL_CONTENT
                    headers["Content-Range"] = f"bytes {part.start}-{part.stop - 1}/{size}"
            headers |= {"Content-Type": get_content_type(name), "Content-Length": str(len(content))}
            self._send_headers(status, headers)
            if body and content:
                self.connection.sendfile(file, content.start, len(content))

    def _send_empty(self, status: HTTPStatus, headers: dict[str, str] | None = None) -> None:
        self._send_headers(status, {"Content-Length": "0"} | (headers or {}))

    def _send_headers(self, status: HTTPStatus, headers: dict[str, str]) -> None:
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.game.shared_memory:
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "credentialless")
        self.end_headers()
