"""Generic service utilities"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from gettext import gettext as _
from pathlib import Path
from typing import TYPE_CHECKING, Any

from qtris import settings
from qtris.config import write_game_config
from qtris.database import sql
from qtris.database.games import DbGameDict, add_game, get_game_for_service, get_games
from qtris.database.services import ServiceGameCollection
from qtris.exceptions import QtrisError
from qtris.game import GAME_UPDATED, Game
from qtris.services.service_media import BANNER_SIZE, ICON_SIZE, ServiceMedia, download_media
from qtris.util import system
from qtris.util.busy import BusyAsyncCall
from qtris.util.cookies import cookie_records, cookies_from_records
from qtris.util.credentials import LegacyCredential, StoreCredentials
from qtris.util.jobs import AsyncCall
from qtris.util.log import logger
from qtris.util.notifications import NotificationSource
from qtris.util.strings import slugify

if TYPE_CHECKING:
    from http.cookiejar import CookieJar

    from requests.cookies import RequestsCookieJar

    from qtris.delegates import Delegate, ServiceLoginUIDelegate


class AuthTokenExpiredError(Exception):
    """Exception raised when a token is no longer valid; the sidebar will
    log-out and log-in again in response to this rather than reporting it."""


class QtrisBanner(ServiceMedia):
    service = "lutris"
    size = BANNER_SIZE
    dest_path = settings.BANNER_PATH
    file_patterns = ["%s.jpg", "%s.png"]
    api_field = "banner"


class QtrisIcon(QtrisBanner):
    size = ICON_SIZE
    dest_path = settings.ICON_PATH
    file_patterns = ["qtris_%s.png"]
    api_field = "icon"
    can_be_fallback = False

    @property
    def custom_media_storage_size(self):
        return (128, 128)

    def run_system_update_desktop_icons(self):
        system.update_desktop_icons()

    def get_fallback_media_path(self, services):
        return None


class QtrisCoverart(ServiceMedia):
    service = "lutris"
    size = (264, 352)
    file_patterns = ["%s.jpg", "%s.png"]
    dest_path = settings.COVERART_PATH
    api_field = "coverart"

    @property
    def config_ui_size(self):
        return (66, 88)


class QtrisCoverartMedium(QtrisCoverart):
    size = (176, 234)


SERVICE_GAMES_LOADING = NotificationSource()
SERVICE_GAMES_LOADED = NotificationSource()
SERVICE_LOGIN = NotificationSource()
SERVICE_LOGOUT = NotificationSource()
# Fired with a service that needs the user to log in again, outside of any
# login the user started; a frontend answers it by calling service.login()
SERVICE_LOGIN_REQUIRED = NotificationSource()


@dataclass(frozen=True)
class StoreLaunch:
    """What a store adds to the command that starts one of its games."""

    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    working_dir: str = ""


class BaseService:
    """Base class for local services"""

    id = NotImplemented
    _matcher = None
    has_extras = False
    name = NotImplemented
    description = ""
    icon = NotImplemented
    online = False
    local = False
    drm_free = False  # DRM free games can be added to Qtris from an existing install
    client_installer = None  # Library slug of the client used by the service
    medias = {}
    extra_medias = {}
    default_format = "icon"
    is_loading = False

    @property
    def matcher(self):
        if self._matcher:
            return self._matcher
        return self.id

    def run(self, launch_ui_delegate):
        """Launch the game client"""
        launcher = self.get_launcher()
        if launcher:
            launcher.launch(launch_ui_delegate)

    def is_launchable(self):
        return bool(self.list_launchers())

    def get_launcher(self):
        launchers = self.list_launchers()
        return Game(launchers[0]["id"]) if launchers else None

    def is_launcher_installed(self):
        launcher = self.get_launcher()
        if not launcher:
            return False
        return launcher.is_installed

    def start_reload(self, reloaded_callback):
        """Refresh the service's games, asynchronously. This raises signals, but
        does so on the main thread- and runs the reload on a worker thread. It calls
        reloaded_callback when done, passing any error (or None on success)"""

        def reload_cb(_result, error):
            logger.debug("Reload callback")
            SERVICE_GAMES_LOADED.fire(self)
            reloaded_callback(error)

        SERVICE_GAMES_LOADING.fire(self)
        AsyncCall(self.reload, reload_cb)

    def reload(self) -> None:
        """Refresh the service's games and artwork on a worker thread."""
        if self.is_loading:
            logger.warning("'%s' games are already loading", self.name)
            return
        self.is_loading = True
        try:
            self.wipe_game_cache()
            self.load()
            self.load_icons()
            self.add_installed_games()
            logger.debug("'%s' games reloaded", self.name)
        finally:
            self.is_loading = False

    def load(self):
        logger.warning("Load method not implemented")

    def load_icons(self):
        """Download all game media from the service"""
        logger.debug("Loading icons...")
        all_medias = self.medias.copy()
        all_medias.update(self.extra_medias)

        service_medias = [media_type() for media_type in all_medias.values()]

        # Download icons
        for service_media in service_medias:
            media_urls = service_media.get_media_urls()
            download_media(media_urls, service_media)

        # Process icons
        for service_media in service_medias:
            service_media.render()

    def wipe_game_cache(self):
        logger.debug("Deleting games from service-games for %s", self.id)
        sql.db_delete(settings.DB_PATH, "service_games", "service", self.id)

    def get_update_installers(self, db_game):
        return []

    def get_store_launch(self, game: Game) -> StoreLaunch | None:
        """Return what one of the store's games needs to start, for stores whose
        client gets involved at each launch; runs on a worker thread."""
        return None

    def forget_installed_game(self, appid: str) -> None:
        """Tell the store's client that Qtris uninstalled one of its games."""

    def generate_installer(self, db_game: dict[str, Any]) -> dict[str, Any]:
        """Used to generate an installer from the data returned from the services"""
        return {}

    def generate_installers(self, db_game: dict[str, Any]) -> list[dict]:
        """Used to generate a list of installers to choose from, from the data returned from the services
        By default this is just the installer from generate_installer(), and if overridden to return
        more than one, then generate_installer must be overridden ti pick a default installer."""
        installer = self.generate_installer(db_game)
        return [installer] if installer else []

    def list_launchers(self) -> list[DbGameDict]:
        """Find registered installations, with the preferred launcher first."""
        if not self.client_installer:
            return []
        launchers: dict[str, DbGameDict] = {}
        for column in ("installer_slug", "slug"):
            for launcher in get_games(filters={column: self.client_installer, "installed": 1}):
                if launcher.get("configpath"):
                    launchers[launcher["id"]] = launcher
        preferred = settings.read_setting(f"{self.id}_launcher", section="services")
        return sorted(launchers.values(), key=lambda entry: entry["id"] != preferred)

    def get_installation_launcher(self) -> DbGameDict:
        """Look up the existing launcher a store-generated installer needs."""
        for launcher in self.list_launchers():
            if launcher.get("directory"):
                return launcher
        raise QtrisError(_("Install the %s launcher and add it to Qtris first.") % self.name)

    def get_installer_files(self, installer, installer_file_id):
        """Used to obtain the content files from the service, when an 'N/A' file is left in
        the installer. Returns a list of InstallerFile or InstallerFileCollection objects
        for the game content files."""
        return []

    def get_dlc_installers_runner(self, db_game, runner, only_owned=True) -> list[dict[str, Any]]:
        """Return DLC installers for requested runner
        only_owned=True only return installers for owned DLC (default)"""
        return []

    def get_extras_files(self, installer, selected_extras):
        """Download the selected extras for a game. Returns a list of InstallerFile objects.

        Only services with has_extras=True should override this; the base
        implementation raises UnavailableGameError."""
        raise NotImplementedError(_("This service does not support extras"))

    def match_existing_game(self, db_games, appid, no_signal=False):
        """Checks if a game is already installed and populates the service info"""
        for _game in db_games:
            logger.debug("Matching %s with existing install: %s", appid, _game)
            game = Game(_game["id"])
            game.appid = appid
            game.service = self.id
            game.save(no_signal=no_signal)
            return game

    def get_installed_slug(self, db_game):
        """Returns the slug the game will have after installation, by default. This
        is Qtris's slug, not the one for the service. By default, we derive it from
        the Game's name."""
        return slugify(db_game["name"])

    def get_installed_runner_name(self, db_game):
        """Returns the name of the runner this game will have after installation, or
        blank if this is not known."""
        return ""

    def get_service_installers(self, db_game, update):
        appid = db_game["appid"]
        service_installers = (
            (self.get_update_installers(db_game) or []) if update else self.generate_installers(db_game)
        )
        # Check if the game is not already installed
        for service_installer in service_installers:
            existing_game = self.match_existing_game(
                get_games(filters={"installer_slug": service_installer["slug"], "installed": "1"}),
                appid,
                no_signal=True,  # we're on a thread here, signals can crash us!
            )
            if existing_game:
                logger.debug("Found existing game, aborting install")
                return None, None, existing_game
        if not service_installers:
            logger.error("No installer found for %s", db_game)
        return service_installers, db_game, None

    def install(self, db_game, ui_delegate: "Delegate", update=False):
        """Install a service game, or starts the installer of the game.

        Args:
            db_game (dict): Database fields of the game to add.
            ui_delegate (Delegate): Starts the installer, if the game needs one.

        Returns:
            str: The slug of the game that was installed, to be run. None if the game should not be
                run now. Many installers start from here, but continue running after this returns;
                they return None.
        """
        logger.debug("Installing %s from service %s", db_game["appid"], self.id)
        # Local services (aka game libraries that don't require any type of online interaction) can
        # be added without going through an install dialog.
        if self.local:
            return self.simple_install(db_game)
        BusyAsyncCall(
            self.get_service_installers,
            lambda result, error: self.on_service_installers_loaded(result, error, ui_delegate),
            db_game,
            update,
        )

    def install_by_id(self, appid, ui_delegate: "Delegate"):
        """Installs a game given the appid for the game on this service."""
        db_game = ServiceGameCollection.get_game(self.id, appid)
        if not db_game:
            logger.error("No game %s found for %s", appid, self.id)
            return None
        return self.install(db_game, ui_delegate)

    def on_service_installers_loaded(self, result, error, ui_delegate: "Delegate"):
        if error:
            raise error  # bounce this error off the backstop for default handling

        service_installers, db_game, existing_game = result

        # If an existing game was found, it may have been updated,
        # and it's not safe to fire this until we get here.
        if existing_game:
            GAME_UPDATED.fire(existing_game)

        if service_installers and db_game:
            ui_delegate.start_installer(service_installers, service=self, appid=db_game["appid"])

    def simple_install(self, db_game):
        """A simplified version of the install method, used when a game doesn't need any setup"""
        installer = self.generate_installer(db_game)
        configpath = write_game_config(db_game["slug"], installer["script"])
        game_id = add_game(
            name=installer["name"],
            runner=installer["runner"],
            slug=installer["game_slug"],
            directory=self.get_game_directory(installer),
            installed=1,
            installer_slug=installer["slug"],
            configpath=configpath,
            service=self.id,
            service_id=db_game["appid"],
        )
        return game_id

    def add_installed_games(self):
        """Services can implement this method to scan for locally
        installed games and add them to qtris.

        This runs on a worker thread, and must no trigger UI actions -
        so no emitting signals here.
        """

    def get_game_directory(self, _installer):
        """Specific services should implement this"""
        return ""

    def get_game_platforms(self, db_game: dict) -> list[str]:
        """Interprets the database record for this game from this service
        to extract its platform, or returns an empty list if this is not available."""
        return []

    def resolve_game_id(self, appid):
        db_game = get_game_for_service(self.id, appid)

        if db_game and db_game.get("id"):
            return str(db_game.get("id"))

        return None

    def get_service_db_game(self, game: Game):
        """Returns the row dictionary for the service-game corresponding to the game given, if any, or None."""
        if game.service == self.id and game.appid:
            return ServiceGameCollection.get_game(self.id, game.appid)
        return None

    def get_store_url(self, db_game: dict) -> str:
        """Return a URL to the game's store page on this service, or empty string."""
        return ""

    def get_game_release_date(self, db_game: dict):
        """Services can implement this method so games that are not in the
        database can be sorted by release date (Year) based on service data."""

    def get_game_release_year(self, db_game: dict):
        """Returns game release year
        Does not have to be rewritten if get_game_release_date returns
        rfc/iso compatible date (YYYY-MM-DD)"""
        date = self.get_game_release_date(db_game)
        return date[:4] if date else ""


class OnlineService(BaseService):
    """Base class for online gaming services"""

    online = True
    cookies_path: str | None = None
    credential_field = "cookies"
    _login_cookies: RequestsCookieJar | None = None
    cache_path = NotImplemented

    login_url = NotImplemented
    login_window_width = 390
    login_window_height = 500
    login_user_agent = settings.DEFAULT_USER_AGENT
    redirect_uris = NotImplemented

    def is_login_complete(self, url):
        """Check if the given URL indicates that login is complete.
        Override this for services where simple startswith matching on
        redirect_uris is not sufficient."""
        return any(url.startswith(r) for r in self.redirect_uris)

    @property
    def credential_files(self) -> list[str]:
        """Return credential files owned by external clients."""
        return []

    @property
    def legacy_credentials(self) -> tuple[LegacyCredential, ...]:
        if self.cookies_path:
            return (LegacyCredential("cookies", self.cookies_path, "cookies"),)
        return ()

    @property
    def credentials(self) -> StoreCredentials:
        return StoreCredentials(Path(settings.CONFIG_DIR, "stores", f"{self.id}.json"), self.legacy_credentials)

    def login(self, ui_delegate: "ServiceLoginUIDelegate") -> None:
        """Log in, asking the user through `ui_delegate`; SERVICE_LOGIN fires once logged in."""
        if self.client_installer and not self.is_launcher_installed():
            ui_delegate.install_service_launcher(self)
            return
        logger.debug("Connecting to %s", self.name)
        ui_delegate.show_web_login(self)

    @property
    def is_login_in_progress(self) -> bool:
        """Set to true if the login process is underway; the credential files may be created at this
        time, but that does not count as 'authenticated' until the login process is over. This is used
        by web logins, which create their cookies before the login is actually complete.

        This is recorded with a file in ~/.cache/qtris so it will persist across Qtris
        restarts, just as the credentials themselves do. For this reason, we need to allow
        the user to login again even when a login is in progress."""
        return self._get_login_in_progress_path().exists()

    @is_login_in_progress.setter
    def is_login_in_progress(self, in_progress: bool) -> None:
        path = self._get_login_in_progress_path()
        if in_progress:
            path.touch()
        else:
            path.unlink(missing_ok=True)

    def _get_login_in_progress_path(self) -> Path:
        return Path(os.path.join(settings.CACHE_DIR, f"{self.name}-login-in-progress"))

    def is_authenticated(self):
        """Return whether the service is authenticated"""
        if self.is_login_in_progress:
            logger.warning("Tried to get auth status while login in progress")
            return False
        return self._login_cookies is not None or bool(self.credentials.read().get(self.credential_field))

    def is_connected(self) -> bool:
        return bool(self.is_authenticated())

    def wipe_game_cache(self):
        """Wipe the game cache, allowing it to be reloaded"""
        if self.cache_path:
            logger.debug("Deleting %s cache %s", self.id, self.cache_path)
            if os.path.isdir(self.cache_path):
                shutil.rmtree(self.cache_path)
            elif system.path_exists(self.cache_path):
                os.remove(self.cache_path)
        super().wipe_game_cache()

    def logout(self):
        """Disconnect from the service by removing all credentials"""
        self.wipe_game_cache()
        self.credentials.clear()
        for auth_file in self.credential_files:
            try:
                os.remove(auth_file)
            except FileNotFoundError:
                continue
            except OSError:
                logger.warning("Unable to remove %s", auth_file)
        logger.debug("logged out from %s", self.id)
        SERVICE_LOGOUT.fire(self)

    def store_cookies(self, cookies: CookieJar) -> None:
        self.credentials.update(cookies=cookie_records(cookies))

    def import_cookies(self, cookies: RequestsCookieJar) -> None:
        self._login_cookies = cookies
        try:
            if not self.is_connected():
                raise QtrisError(_("The store did not accept those cookies. Sign in again and export new cookies."))
            self.store_cookies(cookies)
        finally:
            self._login_cookies = None

    def load_cookies(self) -> RequestsCookieJar | None:
        """Use the candidate cookies during sign-in, or read the saved session."""
        if self._login_cookies is not None:
            return self._login_cookies
        records = self.credentials.read().get("cookies")
        if not records:
            return None
        try:
            return cookies_from_records(records)
        except ValueError:
            raise QtrisError(_("Could not read saved cookies for %s. Sign in again.") % self.name) from None
