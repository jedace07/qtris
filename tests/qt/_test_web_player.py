"""The web player's local server and profile lock."""

from __future__ import annotations

import gzip
import os
import signal
import subprocess
import sys
import time
from http.client import HTTPConnection
from threading import Thread
from typing import TYPE_CHECKING

import pytest

from qtris.qt.web_player.local_server import HOST, TOKEN_HEADER, LocalGameServer
from qtris.util.web_profiles import lock_profile

if TYPE_CHECKING:
    from collections.abc import Generator
    from http.client import HTTPMessage
    from pathlib import Path


@pytest.fixture
def server(tmp_path: Path) -> Generator[LocalGameServer]:
    root = tmp_path / "game"
    (root / "levels").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "index.html").write_text("start", encoding="utf-8")
    (root / "levels" / "index.html").write_text("levels", encoding="utf-8")
    (root / "data.bin").write_bytes(bytes(range(100)))
    (root / "empty.bin").touch()
    (root / ".git" / "config").write_text("hidden", encoding="utf-8")
    (root / "build.wasm.gz").write_bytes(gzip.compress(b"wasm"))
    (root / "level.json.gz").write_bytes(gzip.compress(b"{}"))
    (tmp_path / "secret").write_text("secret", encoding="utf-8")
    (root / "escape").symlink_to(tmp_path / "secret")
    (root / "outside").symlink_to(tmp_path)
    (root / "alias").symlink_to(root / ".git" / "config")
    game = LocalGameServer(root)
    # Not start(), whose half-second poll each test would wait out on shutdown
    Thread(target=game.serve_forever, args=(0.01,), daemon=True).start()
    yield game
    game.shutdown()
    game.server_close()


def fetch(
    server: LocalGameServer, path: str, *, method: str = "GET", token: str | None = None, span: str = ""
) -> tuple[int, HTTPMessage, bytes]:
    headers = {TOKEN_HEADER: server.token if token is None else token}
    if span:
        headers["Range"] = span
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("token", ["", "wrong", "caf\xe9"])
def test_only_the_player_is_answered(server: LocalGameServer, token: str) -> None:
    status, _headers, body = fetch(server, "/index.html", token=token)
    assert (status, body) == (403, b"")


@pytest.mark.parametrize(
    "path",
    [
        "/../secret",
        "/%2e%2e/secret",
        "/..%2fsecret",
        "/%2f..%2f..%2fsecret",
        "/escape",
        "/outside/secret",
        "/.git/config",
        "/alias",
        "/%00",
        "/missing",
    ],
)
def test_nothing_outside_the_game_is_served(server: LocalGameServer, path: str) -> None:
    status, _headers, body = fetch(server, path)
    assert (status, body) == (404, b"")


def test_folders_serve_their_index(server: LocalGameServer) -> None:
    assert fetch(server, "/")[2] == b"start"
    status, headers, _body = fetch(server, "/levels?hard=1")
    assert (status, headers["Location"]) == (301, "/levels/?hard=1")
    assert fetch(server, "/levels/")[2] == b"levels"


@pytest.mark.parametrize(
    ("span", "status", "body"),
    [
        ("bytes=10-19", 206, bytes(range(10, 20))),
        ("bytes=90-", 206, bytes(range(90, 100))),
        ("bytes=-5", 206, bytes(range(95, 100))),
        ("bytes=98-500", 206, bytes(range(98, 100))),
        ("bytes=100-", 416, b""),
        ("bytes=-0", 416, b""),
        ("bytes=20-10", 200, bytes(range(100))),
        ("nonsense", 200, bytes(range(100))),
    ],
)
def test_media_can_seek(server: LocalGameServer, span: str, status: int, body: bytes) -> None:
    response = fetch(server, "/data.bin", span=span)
    assert (response[0], response[2]) == (status, body)


def test_head_and_empty_files_have_no_body(server: LocalGameServer) -> None:
    status, headers, body = fetch(server, "/data.bin", method="HEAD")
    assert (status, headers["Content-Length"], body) == (200, "100", b"")
    assert fetch(server, "/empty.bin")[2] == b""


def test_only_compressed_engine_builds_declare_an_encoding(server: LocalGameServer) -> None:
    _status, headers, _body = fetch(server, "/build.wasm.gz")
    assert (headers["Content-Type"], headers["Content-Encoding"]) == ("application/wasm", "gzip")
    _status, headers, _body = fetch(server, "/level.json.gz")
    assert headers["Content-Encoding"] is None and headers["Content-Type"] == "application/octet-stream"


def test_shared_memory_is_opt_in(server: LocalGameServer) -> None:
    assert fetch(server, "/")[1]["Cross-Origin-Embedder-Policy"] is None
    server.shared_memory = True
    assert fetch(server, "/")[1]["Cross-Origin-Embedder-Policy"] == "credentialless"


def test_the_origin_does_not_follow_the_port(server: LocalGameServer) -> None:
    assert server.get_url(server.root / "index.html", "a=1", "top") == f"http://{HOST}/index.html?a=1#top"
    assert str(server.server_port) in server.resolver_rule


def test_a_profile_has_one_player(tmp_path: Path) -> None:
    with lock_profile(tmp_path / "profile") as first:
        assert first
        with lock_profile(tmp_path / "profile") as second:
            assert not second
    with lock_profile(tmp_path / "profile") as again:
        assert again
    assert (tmp_path / "profile").stat().st_mode & 0o077 == 0


def play_once(tmp_path: Path, page: Path, flags: str) -> str:
    """Run the player until the page reports, stop it as Qtris does and return its output."""
    log = tmp_path / "player.log"
    environment = os.environ | {
        "QT_QPA_PLATFORM": "offscreen",
        "QT_FORCE_STDERR_LOGGING": "1",
        "QTWEBENGINE_CHROMIUM_FLAGS": flags,
        "XDG_CACHE_HOME": str(tmp_path / "qt-cache"),
    }
    command = [
        sys.executable,
        "-P",
        "-m",
        "qtris.qt.web_player",
        f"--profile={tmp_path / 'profile'}",
        f"--cache={tmp_path / 'cache'}",
        page.as_uri(),
    ]
    with log.open("w") as output:
        player = subprocess.Popen(command, cwd=tmp_path, env=environment, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while "saved by" not in log.read_text() and player.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
            player.send_signal(signal.SIGTERM)
            assert player.wait(15) == 0, log.read_text()
        finally:
            player.kill()
            player.wait()
    return log.read_text()


def test_a_local_game_runs_and_keeps_what_it_saves_when_stopped(tmp_path: Path) -> None:
    page = tmp_path / "game" / "index.html"
    page.parent.mkdir()
    page.write_text(
        "<script>"
        "addEventListener('pagehide', () => localStorage.setItem('exit', 'stop'));"
        "console.error('saved by ' + (localStorage.getItem('exit') || 'nobody'));"
        "</script>",
        encoding="utf-8",
    )
    assert "saved by nobody" in play_once(tmp_path, page, "")
    # The user's own Chromium flags mustn't displace the local game's host
    assert "saved by stop" in play_once(tmp_path, page, "--disable-gpu-vsync")
