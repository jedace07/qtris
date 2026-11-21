"""Amazon Games, through Nile"""

import json
import os
from datetime import datetime, timezone
from gettext import gettext as _
from typing import Any
from urllib.parse import parse_qs, urlparse

from qtris import settings
from qtris.exceptions import AuthenticationError
from qtris.game import Game
from qtris.services.base import SERVICE_LOGIN, OnlineService, StoreLaunch
from qtris.services.service_game import ServiceGame
from qtris.services.service_media import ServiceMedia
from qtris.util import nile
from qtris.util.log import logger
from qtris.util.store_cli import StoreClientError
from qtris.util.strings import slugify


class AmazonBanner(ServiceMedia):
    """Game logo"""

    service = "amazon"
    size = (200, 112)
    dest_path = os.path.join(settings.CACHE_DIR, "amazon/banners")
    file_patterns = ["%s.jpg"]
    api_field = "image"
    url_pattern = "%s"

    def get_media_url(self, details: dict[str, Any]) -> str | None:
        return details["product"]["productDetail"]["details"]["logoUrl"]


class AmazonGame(ServiceGame):
    """Representation of a Amazon game"""

    service = "amazon"

    @classmethod
    def new_from_amazon_game(cls, amazon_game):
        """Return a Amazon game instance from the API info"""
        service_game = AmazonGame()
        service_game.appid = str(amazon_game["id"])
        service_game.slug = slugify(amazon_game["product"]["title"])
        service_game.name = amazon_game["product"]["title"]
        service_game.details = json.dumps(amazon_game)
        return service_game


class AmazonService(OnlineService):
    """Amazon Games, through Nile: it registers Qtris as a device, lists the
    library, downloads and updates games, and sets up the Amazon Games SDK that
    games sign in with."""

    id = "amazon"
    name = _("Amazon")
    icon = "amazon"
    runner = "wine"
    has_extras = False
    drm_free = False
    medias = {"banner": AmazonBanner}
    default_format = "banner"
    # Nile keeps the library itself
    cache_path = ""
    redirect_uris = ["https://www.amazon.com/?"]

    def __init__(self) -> None:
        super().__init__()
        self._sign_in: nile.SignInRequest | None = None

    @property
    def credential_files(self) -> list[str]:
        return [os.path.join(nile.CONFIG_PATH, "current_user.json")]

    def is_authenticated(self) -> bool:
        return nile.is_signed_in()

    @property
    def login_url(self) -> str:
        """Start registering Qtris as a device; the user signs in at this address."""
        self._sign_in = nile.begin_sign_in()
        return self._sign_in.url

    def login_callback(self, url: str) -> None:
        """Finish the registration with the address Amazon redirected to."""
        codes = parse_qs(urlparse(url).query).get("openid.oa2.authorization_code")
        if not self._sign_in or not codes:
            raise AuthenticationError(_("Sign in to Amazon again."))
        nile.finish_sign_in(self._sign_in, codes[0])
        self._sign_in = None
        SERVICE_LOGIN.fire(self)

    def logout(self) -> None:
        try:
            nile.sign_out()
        except StoreClientError as ex:
            logger.warning("Nile could not sign out: %s", ex)
        super().logout()

    def load(self) -> list["AmazonGame"]:
        games = []
        for entitlement in _deduplicate(nile.list_games()):
            try:
                game = AmazonGame.new_from_amazon_game(entitlement)
            except (KeyError, TypeError) as ex:
                logger.warning("Skipping an Amazon game Qtris can't read: %s", ex)
                continue
            game.save()
            games.append(game)
        return games

    def get_installed_slug(self, db_game: dict[str, Any]) -> str:
        details = json.loads(db_game["details"])
        return slugify(details["product"]["title"])

    def generate_installer(self, db_game: dict[str, Any]) -> dict[str, Any]:
        details = json.loads(db_game["details"])
        title = details["product"]["title"]
        return {
            "name": title,
            "version": self.name,
            "slug": slugify(title) + "-" + self.id,
            "game_slug": slugify(title),
            "runner": self.runner,
            "appid": db_game["appid"],
            "script": {
                "game": {"prefix": "$GAMEDIR/pfx"},
                "installer": [{"nile_setup": {"game_id": db_game["appid"]}}],
            },
        }

    def get_update_installers(self, db_game: dict[str, Any]) -> list[dict[str, Any]]:
        def installer(command: str, description: str) -> dict[str, Any]:
            return {
                "name": db_game["name"],
                "description": description,
                "slug": db_game["installer_slug"],
                "game_slug": db_game["slug"],
                "version": self.name,
                "runner": db_game.get("runner") or self.runner,
                "script": {
                    "extends": db_game["installer_slug"],
                    "installer": [{"nile_setup": {"game_id": db_game["service_id"], "command": command}}],
                },
            }

        return [
            installer("update", _("Update the game with Nile")),
            installer("repair", _("Verify game files and re-download any that are damaged")),
        ]

    def get_store_launch(self, game: Game) -> StoreLaunch | None:
        """Point the game at the Amazon Games SDK that Nile keeps, which the game
        signs in with."""
        if not game.appid:
            return None
        parameters = nile.get_launch_parameters(game.appid)
        command = parameters.get("command") or {}
        return StoreLaunch(
            args=[str(arg) for arg in command.get("arguments") or []],
            env={str(name): str(value) for name, value in (parameters.get("env") or {}).items()},
        )

    def forget_installed_game(self, appid: str) -> None:
        nile.forget(appid)

    def get_game_release_date(self, db_game: dict) -> str:
        """Return game release date in YYYY-MM-DD format"""
        details = db_game.get("details")
        if details:
            try:
                product = json.loads(details).get("product", {})
                product_details = product.get("productDetail", {})
                details_data = product_details.get("details", {})
                release_date = details_data.get("releaseDate")
                if release_date is not None and isinstance(release_date, str):
                    # Amazon returns ISO 8601 format: "2017-04-13T00:00:00Z"
                    # Parse and normalize to UTC date
                    release_datetime = datetime.fromisoformat(release_date.replace("Z", "+00:00"))
                    return release_datetime.astimezone(timezone.utc).date().isoformat()
            except KeyError, AttributeError:
                pass
        return ""

    def get_installed_runner_name(self, db_game: dict[str, Any]) -> str:
        return self.runner


def _deduplicate(entitlements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Amazon can list a game twice under different IDs; keep one per ASIN, the
    lowest ID, as Qtris always has. Games without an ASIN are all kept."""
    by_asin: dict[str, list[dict[str, Any]]] = {}
    games = []
    for entitlement in entitlements:
        asin = (entitlement.get("product") or {}).get("asin") or ""
        if asin:
            by_asin.setdefault(asin, []).append(entitlement)
        else:
            games.append(entitlement)
    return games + [min(group, key=lambda game: game["id"]) for group in by_asin.values()]
