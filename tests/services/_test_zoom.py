"""Regression coverage for ZOOM's catalog and split installer downloads."""

import json
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError as ResponseError
from urllib.parse import parse_qs, urlsplit

import pytest

from qtris import settings
from qtris.exceptions import QtrisError
from qtris.installer.installer import QtrisInstaller
from qtris.services import zoom
from qtris.services.zoom import ZoomService
from qtris.util.http import HTTPError, Request


def test_catalog_reads_last_page_and_retries_a_throttled_request(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ZoomService()
    pages: list[int] = []
    delays: list[float] = []

    def request_page(request: Request) -> Request:
        page = int(parse_qs(urlsplit(request.url).query).get("page", ["1"])[0])
        pages.append(page)
        if pages == [1, 2]:
            headers = Message()
            headers["Retry-After"] = "3"
            raise HTTPError("Rate limited", code=429) from ResponseError(
                request.url, 429, "Rate limited", headers, None
            )
        request.content = json.dumps({"games": [{"id": page}], "current_page": page, "total_pages": 3}).encode()
        return request

    monkeypatch.setattr(service, "load_token", lambda: "fixture")
    monkeypatch.setattr(Request, "get", request_page)
    monkeypatch.setattr(zoom.time, "sleep", delays.append)

    assert service.get_library() == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert pages == [1, 2, 2, 3]
    assert delays == [3]


def test_failed_catalog_sync_keeps_the_existing_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ZoomService()
    monkeypatch.setattr(service, "wipe_game_cache", lambda: pytest.fail("Failed refresh cleared the catalog"))

    def unavailable() -> list[dict[str, str]]:
        raise QtrisError("ZOOM is unavailable")

    monkeypatch.setattr(service, "get_library", unavailable)

    with pytest.raises(QtrisError, match="ZOOM is unavailable"):
        service.reload()
    assert not service.is_loading


def test_split_installer_files_share_one_directory_and_the_setup_exe_keeps_its_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ZoomService()
    monkeypatch.setattr(settings, "INSTALLER_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "read_setting", lambda *_args, **_kwargs: "")
    requests: list[str] = []

    def get(url: str) -> dict[str, Any]:
        path = urlsplit(url).path
        requests.append(path)
        if path.endswith("/files"):
            return {
                "windows": [
                    {"id": "data", "name": "setup-1.bin", "size": "2 MB"},
                    {"id": "setup", "name": "setup.EXE", "size": "1 MB"},
                    {"id": "extras", "name": "setup-2.bin", "size": "1 MB"},
                ]
            }
        return {"url": "https://cdn.example/" + path.rsplit("/", 1)[1]}

    game = {"name": "Example", "slug": "example", "appid": "42", "details": '{"operating_systems": ["windows"]}'}
    installer = QtrisInstaller(service.generate_installer(game), None, service, "42")
    monkeypatch.setattr(service, "make_request", get)

    (collection,) = service.get_installer_files(installer, "zoominstaller")
    assert collection.num_files == 3 and collection.full_size == 4 * 1024 * 1024
    destinations = {file_id: Path(path) for file_id, path in collection.get_dest_files_by_id().items()}
    assert destinations["zoominstaller"].name == "setup.EXE"
    assert {path.parent for path in destinations.values()} == {tmp_path / "example" / "zoominstaller"}
    collection.prepare()
    assert all(path.parent.is_dir() for path in destinations.values())
    assert requests == ["/li/game/42/files", "/li/download/data", "/li/download/setup", "/li/download/extras"]


def test_bonus_content_resolves_download_links_only_for_selected_files(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ZoomService()
    game = {"name": "Example", "slug": "example", "appid": "42", "details": '{"operating_systems": ["linux"]}'}
    installer = QtrisInstaller(service.generate_installer(game), None, service, "42")
    endpoints: list[str] = []

    def get(url: str) -> dict[str, Any]:
        path = urlsplit(url).path
        endpoints.append(path)
        if path.endswith("/files"):
            return {
                "manual": [{"id": "manual", "name": "manual.pdf", "size": "1 MB"}],
                "soundtrack": [{"id": "music", "name": "music.zip", "size": "3 MB"}],
            }
        return {"url": "https://cdn.example/manual.pdf"}

    monkeypatch.setattr(service, "make_request", get)
    bonus = service.get_extras("42")["extras"]
    assert endpoints == ["/li/game/42/files"]
    (manual,) = service.get_extras_files(installer, bonus[:1])
    assert manual.filename == "manual.pdf"
    assert endpoints == ["/li/game/42/files", "/li/download/manual"]
