"""qtris: links, which launch library games and install store games."""

from __future__ import annotations

import urllib.parse
from typing import TypeAlias

from qtris.util.log import logger

InstallerInfoDict: TypeAlias = dict[str, str | None]


def parse_installer_url(url: str) -> InstallerInfoDict:
    """
    Parses `qtris:` urls, extracting any info necessary to install or run a game.
    """
    action = None
    launch_config_name = None
    try:
        parsed_url = urllib.parse.urlparse(url, scheme="qtris")
    except Exception as ex:
        logger.warning("Unable to parse url %s", url)
        raise ValueError("Invalid qtris url %s" % url) from ex
    if parsed_url.scheme != "qtris":
        raise ValueError("Invalid qtris url %s" % url)
    url_path = parsed_url.path
    if not url_path:
        raise ValueError("Invalid qtris url %s" % url)
    # urlparse can't parse if the path only contain numbers
    # workaround to remove the scheme manually:
    if url_path.startswith("qtris:"):
        url_path = url_path[len("qtris:") :]

    url_parts = [urllib.parse.unquote(part) for part in url_path.split("/")]
    if len(url_parts) == 3:
        action = url_parts[0]
        game_slug = url_parts[1]
        launch_config_name = url_parts[2]
    elif len(url_parts) == 2:
        action = url_parts[0]
        game_slug = url_parts[1]
    elif len(url_parts) == 1:
        game_slug = url_parts[0]
    else:
        raise ValueError("Invalid qtris url %s" % url)

    # To link to service games, format a slug like <service>:<appid>
    if ":" in game_slug:
        service, appid = game_slug.split(":", maxsplit=1)
    else:
        service, appid = "", ""

    return {
        "game_slug": game_slug,
        "action": action,
        "service": service,
        "appid": appid,
        "launch_config_name": launch_config_name,
    }


def format_installer_url(installer_info: InstallerInfoDict) -> str:
    """
    Generates 'qtris:' urls, given the same dictionary that
    parse_intaller_url returns.
    """

    game_slug = installer_info.get("game_slug")
    action = installer_info.get("action")
    service: str = installer_info.get("service")
    appid: str = installer_info.get("appid")
    launch_config_name = installer_info.get("launch_config_name")
    parts = []

    if action:
        parts.append(action)
    elif not launch_config_name:
        raise ValueError("A 'qtris:' URL can contain a launch configuration name only if it has an action.")

    if game_slug:
        parts.append(game_slug)
    else:
        parts.append(service + ":" + appid)

    if launch_config_name:
        parts.append(launch_config_name)

    parts = [urllib.parse.quote(str(part), safe="") for part in parts]
    return urllib.parse.urlunparse(("qtris", "", "/".join(parts), "", "", None))
