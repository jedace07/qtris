"""Module for handling the Zoom service"""

from __future__ import annotations

import json
import os
import time
from gettext import gettext as _
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError as ResponseError
from urllib.parse import parse_qs, urlsplit

from qtris import settings
from qtris.exceptions import AuthenticationError, QtrisError, UnavailableGameError
from qtris.installer import AUTO_ELF_EXE, AUTO_WIN32_EXE
from qtris.installer.installer_file import InstallerFile
from qtris.installer.installer_file_collection import InstallerFileCollection
from qtris.runners import get_runner_human_name
from qtris.services.base import SERVICE_LOGIN, AuthTokenExpiredError, OnlineService
from qtris.services.service_game import ServiceGame
from qtris.services.service_media import ServiceMedia
from qtris.util.credentials import LegacyCredential
from qtris.util.http import HTTPError, Request, UnauthorizedAccessError
from qtris.util.log import logger
from qtris.util.strings import computer_size

if TYPE_CHECKING:
    from qtris.installer.installer import QtrisInstaller


class ZoomCover(ServiceMedia):
    """Small size game logo"""

    service = "zoomplatform"
    size = (200, 300)
    dest_path = os.path.join(settings.CACHE_DIR, "zoom/cover/")
    file_patterns = ["%s.jpg"]
    api_field = "image"
    url_pattern = "%s"


class ZoomGame(ServiceGame):
    """Representation of a Zoom game"""

    service = "zoomplatform"

    @classmethod
    def new_from_zoom_game(cls, zoom_game: dict[str, Any]) -> ZoomGame:
        """Return a Zoom game instance from the API info"""
        service_game = ZoomGame()
        service_game.appid = str(zoom_game["id"])
        service_game.game_id = str(zoom_game["id"])
        service_game.slug = zoom_game["slug"]
        service_game.name = zoom_game["name"]
        details = {**zoom_game, "image": zoom_game.get("poster_url")}
        service_game.details = json.dumps(details)
        return service_game


class ZoomService(OnlineService):
    """Service class for ZOOM Platform"""

    id = "zoomplatform"
    name = _("ZOOM Platform")
    icon = "zoom"
    has_extras = True
    drm_free = True
    medias = {"coverart": ZoomCover}
    default_format = "coverart"

    embed_url = "https://www.zoom-platform.com"
    api_url = "https://www.zoom-platform.com"

    redirect_uris = ["https://www.zoom-platform.com/account?li_token="]

    credential_field = "token"
    token_path = os.path.join(settings.CACHE_DIR, ".zoom.token")
    cookies_path = os.path.join(settings.CACHE_DIR, ".zoom.auth")
    cache_path = os.path.join(settings.CACHE_DIR, "zoom-library.json")

    runner_to_os_dict = {"wine": "windows", "linux": "linux"}

    @property
    def login_url(self) -> str:
        """Return authentication URL"""
        return f"{self.embed_url}/login?li=qtris&return_li_token=true"

    @property
    def legacy_credentials(self) -> tuple[LegacyCredential, ...]:
        return (*super().legacy_credentials, LegacyCredential("token", self.token_path, "text"))

    def is_connected(self) -> bool:
        """Return whether the user is authenticated and if the service is available"""

        try:
            self.make_request(f"{self.api_url}/li/loggedin")
        except AuthenticationError:
            return False
        return True

    def reload(self) -> None:
        """Refresh the catalog without clearing it before the network requests succeed."""
        if self.is_loading:
            return
        self.is_loading = True
        try:
            self.load()
            self.load_icons()
        finally:
            self.is_loading = False

    def load(self) -> list[ZoomGame]:
        """Load the user game library from the Zoom API"""
        try:
            games = [ZoomGame.new_from_zoom_game(game) for game in self.get_library()]
        except AuthenticationError as error:
            raise AuthTokenExpiredError(str(error)) from error
        self.wipe_game_cache()
        for game in games:
            game.save()
        return games

    def login_callback(self, url: str) -> None:
        tokens = parse_qs(urlsplit(url).query).get("li_token", [])
        if len(tokens) != 1:
            raise AuthenticationError(_("ZOOM did not return a sign-in token."))
        token = tokens[0]
        self.make_request(f"{self.api_url}/li/loggedin", token=token)
        self.credentials.update(token=token)
        SERVICE_LOGIN.fire(self)

    def load_token(self) -> str:
        """Load token from disk"""
        token = self.credentials.read().get("token")
        if not isinstance(token, str) or not token:
            raise AuthenticationError(_("Sign in to ZOOM Platform first."))
        return token

    def make_request(self, url: str, *, token: str | None = None) -> dict[str, Any]:
        """Send an authenticated request, honoring short ZOOM rate-limit delays."""
        headers = {"Authorization": f"Bearer {token or self.load_token()}", "Accept": "application/json"}
        for attempt in range(3):
            request = Request(url, headers=headers)
            try:
                request.get()
            except UnauthorizedAccessError:
                raise AuthenticationError(_("ZOOM rejected this session. Sign in again.")) from None
            except HTTPError as error:
                if error.code != 429:
                    raise QtrisError(_("Could not connect to ZOOM Platform.")) from error
                cause = error.__cause__
                retry_after = cause.headers.get("Retry-After", "") if isinstance(cause, ResponseError) else ""
                delay = int(retry_after) if retry_after.isascii() and retry_after.isdecimal() else 1
                if attempt == 2 or delay > 30:
                    raise QtrisError(_("ZOOM is limiting requests. Try again in a moment.")) from None
                time.sleep(delay)
                continue
            try:
                response = request.json
            except ValueError:
                raise QtrisError(_("ZOOM returned an invalid response.")) from None
            if not isinstance(response, dict):
                raise QtrisError(_("ZOOM returned an invalid response."))
            return response
        raise AssertionError("Rate limit retry loop exhausted")

    def get_library(self) -> list[dict[str, Any]]:
        """Fetch every page of the user's ZOOM library."""
        url = f"{self.api_url}/li/games"
        response = self.make_request(url)
        current_page = response.get("current_page", 0)
        total_pages = response.get("total_pages", current_page)
        games = {game["id"]: game for game in response["games"]}
        for page in range(current_page + 1, total_pages + 1):
            response = self.make_request(f"{url}?page={page}")
            games.update((game["id"], game) for game in response["games"])
        return list(games.values())

    def get_extras(self, appid: str) -> dict[str, list[dict]]:
        """Return a list of bonus content available for a Zoom ID and its DLCs"""
        logger.debug("Download extras for Zoom ID %s and its DLCs", appid)
        files_request = self.make_request(f"{self.api_url}/li/game/{appid}/files")
        extras = [
            {
                "id": file["id"],
                "name": file["name"],
                "total_size": computer_size(file["size"]),
            }
            for category in ("manual", "misc", "soundtrack")
            for file in files_request.get(category, [])
        ]
        return {"extras": extras} if extras else {}

    def generate_installer(self, db_game: dict[str, Any]) -> dict[str, Any]:
        installers = self.generate_installers(db_game)
        return installers[0] if installers else {}

    def generate_installers(self, db_game: dict[str, Any]) -> list[dict]:
        details = json.loads(db_game["details"])
        platforms = details["operating_systems"]

        installers = []

        if "linux" in platforms:
            installers.append(self._generate_installer("linux", db_game))

        if "windows" in platforms:
            installers.append(self._generate_installer("wine", db_game))

        if len(installers) > 1:
            for installer in installers:
                runner_human_name = get_runner_human_name(installer["runner"])
                installer["version"] += " " + (runner_human_name or installer["runner"])

        return installers

    def _generate_installer(self, runner: str, db_game: dict[str, Any]) -> dict[str, Any]:
        slug = db_game["slug"]
        game_config = {"exe": AUTO_ELF_EXE if runner == "linux" else AUTO_WIN32_EXE}
        return {
            "name": db_game["name"],
            "version": "Zoom",
            "slug": slug,
            "game_slug": self.get_installed_slug(db_game),
            "runner": runner,
            "zoomid": db_game["appid"],
            "script": {
                "game": game_config,
                "files": [{"zoominstaller": "N/A:Select the installer from Zoom"}],
                "installer": [{"autosetup_zoom_platform": "zoominstaller"}],
            },
        }

    def get_installer_files(self, installer: "QtrisInstaller", installer_file_id: str) -> list[InstallerFileCollection]:
        platform = self.runner_to_os_dict.get(installer.runner)
        if not platform or not installer.service_appid:
            return []
        try:
            installers = self._get_installers(platform, installer.game_slug, installer.service_appid, installer_file_id)
        except AuthenticationError:
            raise
        except QtrisError as error:
            raise UnavailableGameError(error.message) from error
        return [InstallerFileCollection(installer.game_slug, installer_file_id, installers)] if installers else []

    def get_extras_files(self, installer: "QtrisInstaller", selected_extras: list[dict]) -> list[InstallerFile]:
        extras = []
        for index, extra in enumerate(selected_extras):
            filename = extra["name"]
            try:
                self._check_filename(filename)
                url = self._get_download_url(extra["id"])
            except AuthenticationError:
                raise
            except QtrisError as error:
                logger.warning("Unable to locate ZOOM extra '%s': %s", filename, error)
                continue
            extras.append(
                InstallerFile(
                    installer.game_slug,
                    f"zoomextra_{index}",
                    {"filename": filename, "url": url, "total_size": extra["total_size"]},
                )
            )
        return extras

    @staticmethod
    def _check_filename(name: str) -> None:
        if name in ("", ".", "..") or "/" in name or "\\" in name:
            raise UnavailableGameError(_("ZOOM returned an invalid download filename."))

    def _get_download_url(self, file_id: str) -> str:
        url = self.make_request(f"{self.api_url}/li/download/{file_id}").get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise UnavailableGameError(_("ZOOM did not return a valid download link."))
        return url

    def _get_installers(self, platform: str, game_slug: str, appid: str, file_id: str) -> list[InstallerFile]:
        files = self.make_request(f"{self.api_url}/li/game/{appid}/files").get(platform, [])
        if not files:
            return []

        names = [file["name"] for file in files]
        for name in names:
            self._check_filename(name)
        if len({name.casefold() for name in names}) != len(names):
            raise UnavailableGameError(_("ZOOM returned duplicate installer filenames."))

        primary = 0
        if platform == "windows":
            primary = next((index for index, name in enumerate(names) if name.casefold().endswith(".exe")), -1)
            if primary < 0:
                raise UnavailableGameError(_("ZOOM did not provide a Windows setup executable."))

        return [
            InstallerFile(
                game_slug,
                file_id if index == primary else f"{file_id}_part_{index}",
                {
                    "url": self._get_download_url(file["id"]),
                    "filename": file["name"],
                    "size": computer_size(file["size"]),
                },
                cache_id=file_id.replace("-", "_"),
            )
            for index, file in enumerate(files)
        ]

    def get_service_game(self, zoom_game: dict) -> ZoomGame:
        return ZoomGame.new_from_zoom_game(zoom_game)

    def get_store_url(self, db_game: dict) -> str:
        details = db_game.get("details")
        if details:
            slug = json.loads(details).get("slug")
            if slug:
                return f"https://www.zoom-platform.com/product/{slug}"
        return ""

    def get_game_platforms(self, db_game: dict) -> list[str]:
        details = db_game.get("details")
        if details:
            operating_systems = json.loads(details).get("operating_systems", [])
            return [os_name.capitalize() for os_name in operating_systems]
        return []

    def get_game_details(self, product_id: str) -> dict:
        logger.debug("Getting game details for %s", product_id)
        return {}

    def get_installed_runner_name(self, db_game: dict) -> str:
        platforms = [platform.casefold() for platform in self.get_game_platforms(db_game)]
        return "linux" if "linux" in platforms else "wine"
