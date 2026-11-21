*****
Qtris
*****

Qtris helps you install and play video games from all eras and from most
gaming systems. By leveraging and combining existing emulators, engine
re-implementations and compatibility layers, it gives you a central interface
to launch all your games.

The client can connect with existing services like Humble Bundle, GOG and Steam
to make your game libraries easily available. Connected stores supply the game
files and installation options.

Qtris began as a fork of `Lutris <https://github.com/lutris/lutris>`_. It has a
Qt Quick interface, runs its emulators through RetroArch and their Flathub
builds, and keeps your library entirely on your computer.

Running Qtris
=============

Qtris runs either as a Flatpak or straight from the source tree with uv;
``INSTALL.rst`` covers both. Once the system libraries are installed, start
Qtris from the checkout with ``uv run qtris``.

If you need to run Qtris through gdb to troubleshoot segmentation faults, you
can use the following command:

``uv run gdb -ex r --args .venv/bin/python -m qtris.launcher``

Moving from Lutris
------------------

On its first start, Qtris moves an existing Lutris profile to its own folders
(``~/.local/share/lutris`` to ``~/.local/share/qtris``, and the same for
``~/.config`` and ``~/.cache``), along with the Lutris Flatpak's profile when
Qtris runs as a Flatpak. It renames ``lutris.conf`` to ``qtris.conf`` and
updates the folders your games and settings refer to. Lutris itself can't use
that profile afterwards. Desktop and Steam shortcuts that Lutris made still
start Lutris, so recreate them from Qtris.

Installing games
================

Sync a store library, select a game, and choose **Install** in the bar at the
bottom of the window (or right-click the game → **Install**). For a local
Windows setup executable, choose **Add → Run Windows Setup**. Use
**Add → Add Game** to configure a game that is already installed.

Emulators run through the `RetroArch Flatpak
<https://flathub.org/apps/org.libretro.RetroArch>`_ and its libretro cores, or,
for emulators with no core, through their own Flathub builds. Qtris installs
the ones a game needs.

Game library
============

Your library is kept locally. Qtris doesn't connect to Lutris.net accounts, so
it never logs in to the website or uploads your library or play time. It still
downloads public data from Lutris.net, such as install scripts and runtime
versions.

Artwork comes from each game's store (Steam, Epic, GOG...), and from
`SteamGridDB <https://www.steamgriddb.com>`_ for what stores lack and for games
from no store. SteamGridDB needs your own API key, from your SteamGridDB
account; set it in the main menu → Artwork Sources, or in the
``QTRIS_STEAMGRIDDB_API_KEY`` environment variable.

Configuration files
===================

* ``~/.local/share/qtris``: The client, runners, and game configuration files

   There is no need to manually edit these files as everything should be done from the client.

* ``qtris.conf``: Preferences for the client's UI

* ``system.yml``: Default game configuration, which applies to every game

* ``runners/*.yml``: Runner-specific configurations

* ``games/*.yml``: Game-specific configurations

* ``pga.db``: An SQLite database tracking the game library, game installation status, various file locations, and some additional metadata

* ``runners/*``: Wine and Proton builds, and other runner files

* ``banners/*.jpg``: Game banners

``~/.local/share/icons/hicolor/128x128/apps/qtris_*.png``: Game icons

Game-specific configurations overwrite runner-specific configurations, which in
turn overwrite the system configuration.

Command line options
====================

The following command line arguments are available::

-v, --version                    Print the version of Qtris and exit
-d, --debug                      Show debug messages
-b, --output-script              Generate a bash script to run a game without the client
-e, --exec                       Execute a program, monitored by Qtris
-l, --list-games                 List all games in database
-o, --installed                  Only list installed games
-s, --list-steam-games           List available Steam games
--list-steam-folders             List all known Steam library folders
--list-runners                   List all known runners
--list-wine-versions             List all known Wine runners
-a, --list-all-service-games     List all games for all services in database
--list-service-games             List all games for provided service in database
-r, --install-runner             Install a Runner
-u, --uninstall-runner           Uninstall a Runner
-j, --json                       Display the list of games in JSON format
--display=DISPLAY                X display to use
--export <game>                  Exports specified game (requires --dest)
--import <game.7z>               Import games from exportfile (requires --dest)
--dest <folder>                  Specifies Export/Import destination folder

Use a ``qtris:`` link to launch an installed game by slug or library ID::

    qtris qtris:rungame/quake
    qtris qtris:rungameid/42

To open the installer for a synced store game, use
``qtris:install/<source>:<app-id>``. Qtris also opens the ``lutris:`` links
of Lutris.net's install buttons.

Contributing
============

Report bugs and suggest features in the `issue tracker
<https://github.com/jedace07/qtris/issues>`_. ``CONTRIBUTING.md`` explains how
to set up a checkout, run the tests and send changes.

License
=======

Qtris is free software, licensed under the GNU General Public License,
version 3 or later. It contains code from Lutris, copyright the Lutris team
and contributors; see ``AUTHORS``.
