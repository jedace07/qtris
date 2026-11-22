"""The Epic Games Store, through legendary"""

import json
import os
from gettext import gettext as _
from typing import Any

from PIL import Image

from qtris import settings
from qtris.game import Game
from qtris.services.base import SERVICE_LOGIN, OnlineService, StoreLaunch
from qtris.services.service_game import ServiceGame
from qtris.services.service_media import ServiceMedia
from qtris.util import epic_auth, legendary
from qtris.util.images import paste_overlay, thumbnail_image
from qtris.util.log import logger
from qtris.util.store_cli import StoreClientError
from qtris.util.strings import slugify

EGS_LOGO_PATH = os.path.join(settings.CACHE_DIR, "egs/game_logo")


class DieselGameMedia(ServiceMedia):
    service = "egs"
    remote_size = (200, 267)
    file_patterns = ["%s.jpg"]
    min_logo_x = 300
    min_logo_y = 150

    def _render_filename(self, filename: str) -> None:
        game_box_path = os.path.join(self.dest_path, filename)
        logo_path = os.path.join(EGS_LOGO_PATH, filename.replace(".jpg", ".png"))
        has_logo = os.path.exists(logo_path)
        thumb_image = Image.open(game_box_path)
        thumb_image = thumb_image.convert("RGBA")
        thumb_image = thumbnail_image(thumb_image, self.remote_size)
        if has_logo:
            logo_image = Image.open(logo_path)
            logo_image = logo_image.convert("RGBA")
            logo_width, logo_height = logo_image.size
            if logo_width > self.min_logo_x:
                logo_image = logo_image.resize(
                    (self.min_logo_x, int(logo_height * (self.min_logo_x / logo_width))),
                    resample=Image.Resampling.BICUBIC,
                )
            elif logo_height > self.min_logo_y:
                logo_image = logo_image.resize(
                    (int(logo_width * (self.min_logo_y / logo_height)), self.min_logo_y),
                    resample=Image.Resampling.BICUBIC,
                )
            thumb_image = paste_overlay(thumb_image, logo_image)
        thumb_path = os.path.join(self.dest_path, filename)
        thumb_image = thumb_image.convert("RGB")
        thumb_image.save(thumb_path)

    def get_media_url(self, details: dict[str, Any]) -> str | None:
        for image in details.get("keyImages", []):
            if image["type"] == self.api_field:
                return f"{image['url']}?w={self.remote_size[0]}&resize=1&h={self.remote_size[1]}"
        return None


class DieselGameBoxTall(DieselGameMedia):
    """EGS tall game box"""

    size = (200, 267)
    remote_size = size
    min_logo_x = 100
    min_logo_y = 100
    dest_path = os.path.join(settings.CACHE_DIR, "egs/game_box_tall")
    api_field = "DieselGameBoxTall"

    def render(self) -> None:
        if not os.path.isdir(self.dest_path):
            return
        for filename in os.listdir(self.dest_path):
            self._render_filename(filename)


class DieselGameBoxSmall(DieselGameBoxTall):
    size = (100, 133)
    remote_size = (200, 267)


class DieselGameBox(DieselGameBoxTall):
    """EGS game box"""

    size = (316, 178)
    remote_size = size
    min_logo_x = 300
    min_logo_y = 150
    dest_path = os.path.join(settings.CACHE_DIR, "egs/game_box")
    api_field = "DieselGameBox"


class DieselGameBannerSmall(DieselGameBox):
    size = (158, 89)
    remote_size = (316, 178)


class DieselGameBoxLogo(DieselGameMedia):
    """EGS game box"""

    size = (200, 100)
    remote_size = size
    file_patterns = ["%s.png"]
    visible = False
    dest_path = os.path.join(settings.CACHE_DIR, "egs/game_logo")
    api_field = "DieselGameBoxLogo"


class EGSGame(ServiceGame):
    """Service game for Epic Games Store"""

    service = "egs"

    @classmethod
    def new_from_legendary(cls, game: dict[str, Any]) -> "EGSGame":
        """Convert a game legendary lists to a service game; its details are Epic's
        catalog metadata, which the artwork comes from."""
        metadata = game.get("metadata") or {}
        title = game.get("app_title") or metadata.get("title") or game["app_name"]
        service_game = cls()
        service_game.appid = game["app_name"]
        service_game.slug = slugify(title)
        service_game.name = title
        service_game.details = json.dumps({**metadata, "appName": game["app_name"], "title": title})
        return service_game


class EpicGamesStoreService(OnlineService):
    """The Epic Games Store, through legendary: it signs in, lists the library,
    downloads and updates games, and signs each game in to Epic when it starts."""

    id = "egs"
    name = _("Epic Games Store")
    icon = "egs"
    online = True
    runner = "wine"
    medias = {
        "game_box_small": DieselGameBoxSmall,
        "game_banner_small": DieselGameBannerSmall,
        "game_box": DieselGameBox,
        "box_tall": DieselGameBoxTall,
    }
    extra_medias = {
        "logo": DieselGameBoxLogo,
    }
    default_format = "game_banner_small"
    # legendary keeps the library's metadata itself
    cache_path = ""

    @property
    def credential_files(self) -> list[str]:
        return [os.path.join(legendary.CONFIG_PATH, "user.json")]

    def is_authenticated(self) -> bool:
        return legendary.is_signed_in()

    def begin_sign_in(self) -> epic_auth.DeviceLogin:
        """Get a code for the user to approve in their browser."""
        # Before the browser opens, rather than after the user has approved
        legendary.check_available()
        return epic_auth.start()

    def sign_in(self, session: epic_auth.ApprovedSession) -> None:
        epic_auth.hand_over(session, legendary.sign_in)
        SERVICE_LOGIN.fire(self)

    def logout(self) -> None:
        try:
            legendary.sign_out()
        except StoreClientError as ex:
            logger.warning("legendary could not sign out: %s", ex)
        super().logout()

    def load(self) -> list[EGSGame]:
        """Load the list of games"""
        egs_games = []
        # legendary refreshes the sign-in itself; its errors, such as an expired
        # session, reach the user as they are
        for game in legendary.list_games():
            egs_game = EGSGame.new_from_legendary(game)
            egs_game.save()
            egs_games.append(egs_game)
        return egs_games

    def generate_installer(self, db_game: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": db_game["name"],
            "version": self.name,
            "slug": slugify(db_game["name"]) + "-" + self.id,
            "game_slug": self.get_installed_slug(db_game),
            "runner": self.runner,
            "appid": db_game["appid"],
            "script": {
                "game": {"prefix": "$GAMEDIR/pfx"},
                "installer": [{"legendary_setup": {"app_name": db_game["appid"]}}],
            },
        }

    def get_update_installers(self, db_game: dict[str, Any]) -> list[dict[str, Any]]:
        def installer(command: str, version: str, description: str) -> dict[str, Any]:
            return {
                "name": db_game["name"],
                "description": description,
                "slug": db_game["installer_slug"],
                "game_slug": db_game["slug"],
                "version": version,
                "runner": db_game.get("runner") or self.runner,
                "script": {
                    "extends": db_game["installer_slug"],
                    "installer": [{"legendary_setup": {"app_name": db_game["service_id"], "command": command}}],
                },
            }

        return [
            installer('update', _('Update'), _('Download the latest game files.')),
            installer('repair', _('Verify files'), _('Verify game files and re-download any that are damaged.')),
        ]

    def get_installed_runner_name(self, db_game: dict[str, Any]) -> str:
        return self.runner

    def get_store_launch(self, game: Game) -> StoreLaunch | None:
        """Sign the game in to Epic: legendary gives the parameters the Epic Games
        Launcher would, with a one-time code for the game's sign-in."""
        if not game.appid:
            return None
        parameters = legendary.get_launch_parameters(game.appid)
        args = [*parameters.get("game_parameters", []), *parameters.get("egl_parameters", [])]
        return StoreLaunch(
            args=[str(arg) for arg in args],
            env={str(name): str(value) for name, value in (parameters.get("environment") or {}).items()},
            working_dir=str(parameters.get("working_directory") or ""),
        )

    def forget_installed_game(self, appid: str) -> None:
        legendary.forget(appid)

    def get_store_url(self, db_game: dict[str, Any]) -> str:
        details = json.loads(db_game.get("details") or "{}")
        for mapping in details.get("catalogNs", {}).get("mappings") or []:
            if mapping.get("pageType") == "productHome" and mapping.get("pageSlug"):
                return f"https://store.epicgames.com/p/{mapping['pageSlug']}"
        return ""
