"""The library's own artwork formats, which qtris.artwork fills in."""

from qtris.services.base import QtrisBanner, QtrisCoverart, QtrisCoverartMedium, QtrisIcon


class QtrisMedia:
    """The library's artwork for games, by slug, for views that pick their formats
    from a service. It isn't one: qtris.artwork downloads it from each game's store."""

    id = "lutris"
    medias = {
        "icon": QtrisIcon,
        "banner": QtrisBanner,
        "coverart_med": QtrisCoverartMedium,
        "coverart_big": QtrisCoverart,
    }
    default_format = "coverart_med"
