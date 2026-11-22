"""Start the web player: python -P -m qtris.qt.web_player [options] URL."""

from __future__ import annotations

import logging
import sys

from qtris.util.textdomain import bind_text_domain


def main() -> int:
    bind_text_domain()
    # Standard error is the game's log in Qtris
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    from qtris.qt.web_player.options import parse_options
    from qtris.qt.web_player.player import run

    return run(parse_options(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
