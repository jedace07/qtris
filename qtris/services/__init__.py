"""Service package"""

from typing import TYPE_CHECKING

from qtris import settings
from qtris.services.amazon import AmazonService
from qtris.services.battlenet import BattleNetService
from qtris.services.ea_app import EAAppService
from qtris.services.egs import EpicGamesStoreService
from qtris.services.flathub import FlathubService
from qtris.services.gog import GOGService
from qtris.services.itchio import ItchIoService
from qtris.services.steam import SteamService
from qtris.services.ubisoft import UbisoftConnectService
from qtris.services.xdg import XDGService
from qtris.services.zoom import ZoomService
from qtris.util import flatpak
from qtris.util.linux import LINUX_SYSTEM

if TYPE_CHECKING:
    from qtris.services.base import BaseService

# Default to stores with a native client or host installation flow
DEFAULT_SERVICES = ["steam", "gog", "egs", "amazon", "flathub"]


def get_services() -> dict[str, "type[BaseService]"]:
    """Return a mapping of available services"""
    _services = {
        "gog": GOGService,
        "zoomplatform": ZoomService,
        "egs": EpicGamesStoreService,
        "itchio": ItchIoService,
        "ea_app": EAAppService,
        "ubisoft": UbisoftConnectService,
        "amazon": AmazonService,
    }
    _services["battlenet"] = BattleNetService
    if not LINUX_SYSTEM.is_flatpak():
        _services["xdg"] = XDGService
    if LINUX_SYSTEM.has_steam():
        _services["steam"] = SteamService
    if flatpak.is_installed():
        _services["flathub"] = FlathubService
    return _services


SERVICES = get_services()


def get_enabled_services():
    return {
        key: _class
        for key, _class in SERVICES.items()
        if settings.read_setting(key, section="services").lower() == "true"
    }
