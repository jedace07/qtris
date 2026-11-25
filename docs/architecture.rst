Architecture
============

Where this is going
-------------------

Qtris has a toolkit-independent **core** and two **frontends**: the command
line (``qtris/cli``) and the Qt Quick desktop application (``qtris/qt``).
Both drive the core through ``qtris.backend``, an in-process API of plain-data
calls and events. The GTK frontend has been removed. A future stage could put
the same API behind IPC so the core can run as a separate process.

The rule
--------

**The core never imports a frontend or a GUI toolkit.** ``GLib``, ``GObject``
and ``Gio`` aren't UI and are allowed; accessing the running application
through ``Gio.Application.get_default()`` stays in the frontend.
``tests/_test_architecture.py`` also keeps the CLI toolkit-free and guards the
Qt Quick boundary. The only Qt Widgets import in the application is
``QApplication`` for bootstrap. The web player, a program of its own, uses
Widgets too (see "Web games").
``tests/_test_imports.py`` checks that each core module can be imported on its
own, which catches the circular imports that moving code tends to create.

How the core reaches a frontend
-------------------------------

The core never calls into a frontend directly. These are the ways it gets one
to do something, and new code should use them:

The main loop: ``qtris.util.mainloop``
    All deferred work (``schedule_at_idle``, ``AsyncCall`` callbacks,
    notifications) runs on the installed ``MainLoop``. The default is GLib's,
    which the Qt and CLI frontends both use; a toolkit that didn't would install its own with
    ``set_main_loop()`` before it starts.

Displays: ``qtris.util.display_backend``
    What only the UI toolkit knows about the screen (whether it's X11, the
    primary monitor's scale, and how to keep the computer awake when no
    screen saver D-Bus interface will) comes from the installed
    ``DisplayBackend``. ``qtris.util.display`` asks it, and prefers Mutter's
    D-Bus interface for Wayland display modes. On X11, the fallback is xrandr.
    ``qtris.qt.display_backend.QtDisplayBackend`` reads Qt's platform and
    screen properties and uses available session services to inhibit sleep.

Background work: ``qtris.util.jobs``
    ``AsyncCall`` runs a function on a thread and delivers its result on the
    main loop. ``callback_target`` cancels delivery if its object reports being
    destroyed (anything with ``call_when_destroyed``). Frontends on the
    backend's API use asyncio tasks instead, and cancel them.

Notifications: ``qtris.util.notifications``
    ``NotificationSource`` is how the core announces that something happened:
    ``GAME_STARTED``, ``GAME_UPDATED``, ``SERVICE_GAMES_LOADED``,
    ``CATEGORIES_UPDATED``, ``RUNNERS_INSTALLED`` (fired with the installer
    interpreter that installed them) and so on. Handlers always run on the main loop,
    never inline. These are what will become the server's event stream.

Delegates: ``qtris.delegates``
    When the core needs the user (confirm a launch, pick a launch
    configuration, answer an installer question, provide a file, log in to a
    service), it calls a delegate passed in by the caller. The base
    ``Delegate`` has the general questions (``show_notice``, ``ask_question``,
    ``ask_for_text``, ``ask_for_file``, and ``start_installer`` to run an
    installer script with the user); ``LaunchUIDelegate``,
    ``InstallUIDelegate`` and ``ServiceLoginUIDelegate`` add what launching,
    installing and logging in need. The base classes make the non-interactive
    choice. Qt handles launch questions with ``PromptController`` and QML.
    Installer questions use plain-data snapshots and responses from
    ``qtris.backend.installations``.

    A game keeps the delegate it was launched with until it quits, for the
    questions that come after the launch: syncing GOG cloud saves asks it which
    saves to keep through ``resolve_cloud_sync_conflict`` (a
    ``ConflictResolver``), and skips conflicts when given none.

    When the core needs the user and no one asked it to do anything (a
    service's login expiring in the background), it fires a notification
    instead (``SERVICE_LOGIN_REQUIRED``) and a frontend answers it by
    starting the operation with its own delegate.

Errors
    The core doesn't show errors. A game's operations are wrapped in
    ``qtris.game_errors.watch_game_errors``, which stops the game and reports
    the error through the game's ``game_error`` or, when nothing handles that,
    the ``GAME_UNHANDLED_ERROR`` notification. Catching what escapes event-loop
    callbacks is the frontend's job. Qt reports errors from its slots through
    ``sys.excepthook`` and from its tasks through ``qtris.qt.tasks``.

Models instead of widgets
    When the core produces something a frontend displays as it changes, it
    keeps it in a plain object with a notification, and the frontend adapts
    that to its own widgets. For example:

    * ``qtris.util.log_buffer.LogBuffer``: games and installer commands write
      their output to one; Qt reads it through the game and installation APIs.
    * ``qtris.services.service_media``: services download game artwork
      with ``download_media`` and process it with Pillow
      (``qtris.util.images``), then fire ``MEDIA_CACHE_INVALIDATED``; frontends
      only load and draw the files.
    * ``qtris.util.file_downloads``: ``SingleFileDownload`` and
      ``FileCollectionDownload`` run an installer's downloads (queueing,
      retries, the download cache) and publish their progress as
      ``DownloadStatus`` data, which the backend exposes to Qt in the
      installation snapshot.

    * ``qtris.services.gog_cloud_operation``: ``start_cloud_sync`` runs a GOG
      game's cloud save sync before it launches and after it quits, in order,
      and announces each ``CloudSyncOperation`` with ``CLOUD_SYNC_STARTED`` and
      ``CLOUD_SYNC_ENDED``. ``qtris.backend.cloud_saves`` exposes transfer
      progress and skipping. Closing the window doesn't interrupt a post-exit
      transfer.

    Notifications are delivered later, on the main loop, so a model that
    changes quickly should pass a snapshot of its state with each one rather
    than have handlers read its current state (the downloads do).

Web games
---------

The Web runner starts ``qtris.qt.web_player`` as a process for each game, by
module name, so the core still imports no frontend. A game's process is tracked
and stopped like any other game's, and shares nothing with the application's
QtWebEngine or with another game's.

Profiles
    A game's site data (cookies, storage, service workers) is in
    ``DATA_DIR/web-profiles/<game id>-<time added>`` and its cache in the same
    folder under ``CACHE_DIR``. Renaming or moving the game keeps it. The time is
    there because SQLite reuses the ids of deleted games. A file lock keeps a
    second player out of a profile. When removing a game from the library, the
    user can move its persistent browser profile to the desktop Trash. Leaving
    the option unchecked keeps its saved data on disk.

Qt Widgets
    The player uses ``QWebEngineView``. Qt Quick's ``WebEngineView`` denies every
    pointer lock request ("Not supported in Qt Quick", in QtWebEngine's
    ``qquickwebengineview.cpp``), which breaks mouse-look games.

Local games
    An HTML file is never loaded as ``file:``, which lets a page read any of the
    user's files. The player serves the file's folder on a random loopback port,
    answers only requests carrying its token, and maps the fixed host
    ``game.qtris.localhost`` to the port, so the game's origin and saves outlast
    it. Hidden files and links leading out of the folder aren't served, and the
    runner refuses a file kept directly in the home or another personal folder.
    The *Shared memory for local games* option adds the cross-origin isolation
    headers that threaded builds need.

Stopping
    Chromium loses its latest writes if a signal kills it, so the player turns
    SIGTERM, SIGINT and SIGHUP into a close: it unloads the page, waits for the
    storage to commit and exits within about two seconds. The runner's Stop
    signals only the player, not the engine's processes under it.

Permissions
    Pointer lock and writing to the clipboard are allowed; camera, microphone,
    location, notifications and reading the clipboard are denied. Pop-ups need a
    click. Links to other sites that open a tab go to the desktop portal, as a
    browser started by the player would count as part of the game.

The command line
----------------

Every ``qtris`` command starts in ``qtris.cli.application``'s
``CommandLineApplication``, a ``Gio.Application`` with the same id as the Qt
frontend's, so Qtris still runs once:

* A command that needs no GUI (listing games or runners, installing a runner,
  ``--export``, ``-b``, ``-e`` and so on, ``qtris.cli.options.TEXT_COMMANDS``)
  is forwarded to the Qtris instance already running, whatever its frontend,
  and runs there; with none running, it runs in the ``qtris`` process itself,
  with no GUI toolkit loaded. The commands live in ``qtris.cli.commands``,
  which the Qt frontend calls for the command lines forwarded to it. They
  print, ask and resolve relative paths through ``CommandOutput``, so they reach
  the terminal that ran ``qtris`` even when another process runs them.
* Any other command line (no arguments, a ``qtris:`` URI,
  ``--submit-issue``) needs a GUI: ``CommandLineApplication`` exits before
  registering, and the launcher runs the Qt frontend with it instead.

Both frontends define the same options (``qtris.cli.options``), as Gio
forwards a command line with its options already parsed.

Stage 2: the backend's API
--------------------------

``qtris.backend`` is the front door. Frontends drive Qtris through it, and
eventually through nothing else:

* **Every call is a coroutine** (``await games.list_games()``). Over IPC each
  call becomes a request and its reply, so a remote frontend will await the same
  calls with the same signatures. Calls run on the frontend's asyncio event
  loop, and send slow work (the network, large files) to a thread with
  ``asyncio.to_thread``, so awaiting one never blocks the UI.
* **Calls take and return plain data**: ids, strings, JSON-like dictionaries,
  and the frozen dataclasses in ``qtris.backend.models`` (``GameInfo``,
  ``ServiceGameInfo``, ...), never a live ``Game``, runner or service object.
* **Errors are** ``QtrisError`` **subclasses** (``qtris.backend.errors``), whose
  message is meant for the user.
* **Calls that need the user take a delegate**, as the core does.
* **Events carry plain data too**: ``qtris.backend.events`` re-announces the
  core's game notifications with a ``GameInfo`` (``GAME_CHANGED``,
  ``GAME_INSTALLED``, ``GAME_LAUNCHING``, ``GAME_STARTED``, ``GAME_STOPPED``)
  or a game's id (``GAME_LAUNCH_STATUS``, ``GAME_FAILED``). A launch is
  followed by ``GAME_STARTED`` or ``GAME_STOPPED`` whatever happens, so a
  frontend tracks a game's state from these instead of asking a ``Game``. Over
  IPC they become the server's event stream.

It's grouped by subject: ``games``, ``library`` (what the library's views
list: ``query_games`` takes a ``LibraryQuery``, the view's sidebar row, search
and sort, and returns ``LibraryGame`` rows), ``services``, ``steam``,
``runners``, ``installers`` (preparing and checking installation plans),
``installations`` (running them), ``configuration`` (game, runner and system
options),
``processes`` (running a command the way Qtris runs games) and ``saves``.
``qtris.backend.startup.start_backend()`` starts it; that isn't an API call,
but what the backend's own process will do in stage 3.

asyncio runs on GLib's main loop: ``qtris.launcher`` calls
``qtris.util.mainloop.run_asyncio_on_glib()``, after which PyGObject runs
asyncio whenever a ``Gio.Application`` runs. Qt dispatches its events through
GLib too, so ``Gio.Application.run()`` drives Qt, GLib and asyncio.
``qtris.qt.tasks.start_task()`` uses ``Gio.Application.create_asyncio_task()``
to keep the application running while a call finishes and surface errors.
Forwarded commands report their status with ``set_exit_status()`` and ``done()``.

``tests/_test_frontend_api.py`` enforces this: a frontend may import
``qtris.backend``, its own package and a few support modules (logging,
strings, settings, notifications, delegates), and nothing else from the core.
Qt can also import the shared command handling in ``qtris.cli``. The CLI
must not import Qt.

The Qt Quick frontend
---------------------

PySide6 Essentials is a required dependency in source installs and the Flatpak.
The tests run on Qt's offscreen platform.

The frontend uses Qt Quick Controls on a PySide6 ``QApplication``. Qt runs on
GLib's main loop, which ``Gio.Application.run()`` runs, and ``qtris/qt/glib_loop.py``
emits ``aboutToBlock`` before each of its waits, as Qt's own loop does: Qt's
Wayland backend only sends its requests to the compositor then.

The tray icon (``qtris/qt/tray``) is Qtris's own StatusNotifierItem and DBusMenu
over Gio, not ``QSystemTrayIcon``, whose icon tells the desktop to send clicks to
the application rather than open its menu. Clicking Qtris's icon opens the menu,
as AppIndicator icons do. The icon has a D-Bus connection of its own, which
closing takes off the tray; tray availability is followed through the
watcher's signals.
``qtris/qt/qml`` defines the windows, views and dialogs.
Every control is drawn by ``QtrisStyle`` (``qtris/qt/qml/QtrisStyle``), a
Qt Quick Controls style that ``MainWindow`` selects with ``QQuickStyle`` before
loading QML, so plain ``import QtQuick.Controls`` gets the Qtris look. Its
``Theme`` singleton holds the colors, metrics and type sizes for light and dark,
following the theme preference and, for "System Default", the desktop's color
scheme. ``Theme.icon()`` names an SVG of the style's icon set, and ``Icon`` draws
one tinted to the text around it. Views reach for ``Theme`` rather than literal
colors, so both themes stay consistent.

The window has a sidebar of library views, a header with search, the installed
filter, sorting, the layout switcher, the **Add** menu and the main menu, and a
bar under the library for the selected game, with Play, Stop or Install and its
Game Actions menu (also on right-click).
``LibraryController`` exposes Qt properties, model roles and slots to QML.
It queries the backend, handles its events, and translates launch prompts
into QML dialogs. QML strings use the application's gettext catalogs.

The library has search, sorting, an installed filter, and sidebar views
including categories, sources and saved searches. It launches and stops games
through the backend. The Game Actions menu can add or remove favorites, hide
or unhide a game, edit its categories, and open its installation directory.
It also shows live game logs and manages desktop, application menu, and Steam
shortcuts. The Steam Big Picture (Standalone) destination exports an executable
script for the selected launch configuration and adds it to Steam separately
from the regular shortcut. The script launches without Qtris; recreate the
shortcut after changing the launch configuration. Qt can remove legacy
standalone entries without deleting their old shared scripts. Standalone
creation is not offered from inside Flatpak because exported commands can refer
to sandbox-only paths.

When a game has multiple launch configurations, Qt can prompt for one and
remember the choice. ``qtris:rungame/...`` and ``qtris:rungameid/...``
launch installed library games, including the launch target encoded in a shortcut.
A shortcut uses the running instance if there is one. Log updates use
``GAME_LOG_CHANGED`` and ``games.get_game_log``.

Game Actions can move a local installation to another folder, uninstall it,
or remove its library entry. Uninstalling keeps the files and library entry
unless the user selects otherwise. The ``game_maintenance`` backend checks
again before moving or trashing files and blocks protected, overlapping, and
externally managed installations. Qt refreshes the library on ``GAME_CHANGED``
or ``GAME_REMOVED``.

The Add menu can add an existing native Linux executable as a new game.
For an eligible uninstalled entry, Game Actions can link the executable to
that entry and keep its ID, category membership and playtime. The
``local_games`` backend validates the path and writes the launch configuration
before updating the library and firing ``GAME_INSTALLED``. It deliberately
does not record a managed installation directory: Browse Files still opens
the executable's folder, but Move and Trash Files cannot act on it from that entry.
Store-owned entries, non-Linux runners, and installer scripts are not handled
by this flow.

Game Actions → Install works on synced source rows, including those that
have yet to be added to the local library. ``installers.find_store_installers``
asks the source for its plans in a worker thread and returns an
``InstallerCatalog`` of ``InstallerChoice`` metadata. GOG offers Update or Repair and Install DLC for installed games,
and itch.io offers updates. Library → Run
Windows Setup uses ``prepare_windows_setup`` to run a local executable with
Wine/Proton and the selected Windows version and locale. Every plan comes from a
store or from Qtris itself: Lutris.net's community install scripts, uploaded
YAML installers and the script directives only they used are gone, and Qtris
makes no requests to Lutris.net. ``qtris:install/<source>:<app-id>`` opens the
same store installer.

Plans stay in the backend until ``close_catalog``. ``select_installer``
checks a copy in a worker thread and returns the default folder, any
required runners, and any missing dependency. ``start_installation``
prepares runners, extras and files, then calls the interpreter. Updates and
DLC use the selected installed game's ID and directory. A cancelled update
or DLC install preserves the base game directory.

Each stage fires ``INSTALLATION_CHANGED`` with an ``InstallationInfo``
snapshot. The frontend responds to runner and interpreter questions with
``answer_runner``, ``choose_extras``, ``set_file_source``,
``start_files``, ``answer_menu`` or ``answer_disc``. File downloads
run three at a time. For Steam data, Qtris requests the install through
Steam and waits for its appmanifest to report a complete installation.
Cancelling that wait leaves Steam's own installation in progress.
``InstallationPage`` shows progress and command output, and completion
fires ``GAME_INSTALLED``. New installs can create shortcuts and launch the
game from the dialog.

Game Actions → Configure, the main menu's Global Options and a local platform's context menu
in the sidebar open configuration forms. ``configuration.open_game_config``,
``open_runner_config`` and ``open_system_config`` build a ``QtrisConfig`` at
that level and return a ``ConfigForm``: a ``ConfigPage`` per config section
(game, runner, system), each with ``ConfigOptionInfo`` rows. The backend
evaluates the option dicts' callables with
the option key and the configuration, so visibility, conditions,
``conditional_on``, choices, warnings and errors arrive as plain values.
``set_option`` and ``reset_option`` change the raw config at the form's level,
checked against the option's type, and return the form evaluated again.
``save_config`` runs the options' validators, writes the file and fires
``GAME_CHANGED`` for a game. A game's form also carries its ``GameDetails``
(name, sort name, slug, runner, year, playtime), changed with
``set_game_details`` and written by ``save_config``, which validates them and
renames the config file when the slug changes.
``set_game_runner`` starts the game's configuration afresh for another runner.
``open_new_game_config`` is Add → Add Game: saving it creates the game.
Qt shows the details on a Game Info tab before the option pages. Qt's ``ConfigOptionModel`` updates rows in place,
so editing doesn't reset the view, and ``ConfigOption.qml`` picks an editor per
option type.

For native Linux games, Platform options → Steam Linux Runtime takes effect
for the game or as the Linux runner's default. None uses the system libraries.
The other choices use Valve's 1.0 (Scout), 2.0 (Soldier), 3.0 (Sniper), or 4.0
container. Qtris finds the launchers in Steam libraries or in umu's runtime
directory. Scout uses the Steam compatibility tool inside Soldier. If a
runtime is missing, the launch error gives its Steam install URI. The runner
applies the choice to primary and alternate launch configurations, including
standalone shortcuts.

The dedicated Flatpak runner and Flathub game source have been removed.
Existing library records can still be removed without changing the installed
apps.

Runtime updates run in the backend (``runtime_updates.update_runtime``), which
announces each component's progress with ``RUNTIME_UPDATE_CHANGED``. Qt runs
them at startup when the last check is stale, shows their progress in the status
bar, and Preferences → Updates checks again on request.

The library opens in a cover grid. The View menu switches to banners or a list
and remembers the choice. Grid queries ask for ``LibraryArtwork`` with each
row. The backend resolves files from the Qtris and service caches. The same
menu downloads missing artwork with ``media.update_artwork``, which Qt also
runs quietly at startup. The grid reloads its thumbnails when it receives
``ARTWORK_CHANGED``.

``qtris.artwork`` fills in each library game's cover, banner and icon (the
``coverart``, ``banners`` and icon files the library reads by slug). It asks the
game's store first: Steam's 2:3 library capsule and header, Epic's tall and wide
box art, GOG's vertical cover from its public game database and its product
image, and for other services the largest portrait and landscape formats their
media classes know. SteamGridDB fills in what's left, and serves games that come
from no store: it's searched by Steam app id or by name, for 2:3 grids (600x900,
or its near 2:3 sizes) and 920x430-shaped ones, and for icons. SteamGridDB needs
the user's API key, saved through ``media.set_steamgriddb_api_key`` (which checks
it first) or given in ``QTRIS_STEAMGRIDDB_API_KEY``; View → Artwork Sources in
Qt sets it. Only missing files are downloaded, so custom artwork is never
replaced, and what no source has is remembered for a week in
``artwork-misses.json``, kept apart for with and without SteamGridDB, so that adding
a key retries at once.

The Game Info tab also shows the game's own cover, banner and icon
(``game_artwork``). Choosing a file prepares it in Qt: ``QImage`` loads and
scales it down to the size it's stored at off the GUI thread (``QPixmap`` only
works on it) and saves PNG or JPEG, which ``set_custom_artwork`` stores.
``list_steamgriddb_artwork`` and ``use_artwork_url`` offer SteamGridDB's images
to choose from. Chosen artwork is marked custom in the library, so it stays,
and ``reset_artwork`` removes it, forgets past misses and fetches the automatic
artwork again.

Game Info also resets the remembered launch configuration and opens Move for
managed installations. Launching waits for a pending runtime update and can
prompt for a missing runner. GOG cloud conflicts prompt only when a window is
available; otherwise the conflict is skipped rather than silently overwritten.

Library → Preferences includes the interface theme, tray, game card labels and
badges and minimize-on-launch options. The tray is available
only on desktops that provide one. Library also offers a local JSON issue
report exporter shared with ``--submit-issue``.

Qtris has three runners, Proton (``wine``), Linux and Web, and two internal
ones for their sources' games, Steam and Flatpak. It has no emulator runners:
emulators are Flathub apps, which the Flathub source lists and launches. GOG's
and ZOOM's DOS and ScummVM games run the Windows DOSBox or ScummVM they bundle
through Proton. For GOG's depot downloads the installer copies their settings
from ``gog-support/<id>/app``, where gogdl leaves them, into the game's folder.

Preferences → Local Games opens the settings for Linux, Windows and Web. These
platforms are available in every profile. Under Windows, **Manage Proton** installs,
updates and removes builds through ``qtris.backend.runners``. Downloads and file
prompts pass progress and plain text back to Qt while installation runs in a
worker thread. The page has a tab for each Proton fork Qtris downloads, GE-Proton and
Proton-CachyOS (``qtris.util.wine.proton_releases``), listing installed builds
and recent releases for the host architecture from GitHub. Proton-CachyOS gets
its x86-64-v3 build when the CPU supports that level. Downloads are checked
against their SHA-512 checksums and installed into Qtris's own Proton folder
(``settings.PROTON_DIR``, ``runners/proton`` in Qtris's data folder), not
Steam's ``compatibilitytools.d``. Builds Qtris installed there before are still
listed and can be removed; other Proton locations are listed without a remove
action.

Each tab starts with a rolling build (``qtris.util.wine.rolling_proton``),
GE-Proton Latest or Proton-CachyOS Latest, which work like ProtonPlus's and
Heroic's Latest builds: one ``GE-Proton Latest`` or ``Proton-CachyOS Latest``
folder that Qtris replaces with each new release. An update verifies and
unpacks the release next to the folder, carries over ``user_settings.py``,
records the release in ``.qtris-rolling.json`` and swaps the folder in, keeping
the previous build until the new one is in place; a build a running game uses
is left alone. Updates run with the other components (Preferences → Updates,
following the automatic update setting) and from the tab's Update button, and a
game set to one installs it before launch. GE-Proton Latest is the default.

umu keeps the Steam Linux Runtime it downloads for Proton (and its locks) in
``settings.UMU_DATA_DIR``, ``runtime/umu-data`` in Qtris's data folder, through
``UMU_FOLDERS_PATH``, rather than the host's ``~/.local/share/umu``. The native
Linux runner's Steam Linux Runtime option finds those runtimes there too.

The Wine runner runs Proton only: its version picker offers GE-Proton Latest,
Proton-CachyOS Latest, GE-Proton and Proton-CachyOS releases by release name,
Valve's Proton and other Proton builds, with no system Wine, Wine builds or
custom executables. A configured version that isn't Proton falls back to
GE-Proton Latest, except in a Wine prefix Proton has never run in.

Selecting a source in Qt exposes **Sign In**, **Sync Library**, and
**Disconnect** as appropriate. GOG, Amazon and ZOOM Platform sign in in
Qtris's own web view (``StoreWebLoginWindow.qml``, QtWebEngine) to capture the
response from the store's sign-in redirect. Each sign-in gets a private,
off-the-record profile whose user agent drops the QtWebEngine token.
``store_login.is_web_response()`` tells the frontend which address ends the
sign-in. GOG also keeps the web session's ``gog.com`` cookies, though its API
token alone signs in. Qtris captures ZOOM's token before the callback page
can redirect again and validates it with ZOOM before saving it.

The Epic Games Store uses Legendary's sign-in page in the system default
browser. The user copies the final JSON response and returns to Qtris.
While the Epic sign-in dialog is open, Qtris checks for a newly copied
``authorizationCode``. The dialog also accepts a code entered manually.
Qtris passes the code to ``legendary auth --code`` to save the session.

EA App signs in through the system default browser and accepts JSON and
Netscape cookie exports. Qtris keeps current cookies for the selected store
and its subdomains.

Qtris-managed credentials live in ``stores/<store-id>.json`` under Qtris's
configuration folder. Qtris writes the files atomically with owner-only
permissions and migrates old cache-based credentials on first use. GOG and
EA App keep their tokens and cookies together. gogdl receives its auth file
in a private temporary directory for the duration of the command.

itch.io can use browser OAuth with PKCE for owned games and their downloads.
To enable it, register a Qtris OAuth application with the callback
``http://127.0.0.1:34567/callback`` and launch Qtris with its public client ID
in ``QTRIS_ITCHIO_CLIENT_ID``. Qtris chooses an available loopback port during
sign-in. Users can use a dedicated API key to include their itch.io collections.
The API key flow is the default until a client ID is configured.

``store_login`` retains the service instance until the handoff completes so it
can keep transient login data such as the device registration Nile starts for
Amazon. The frontend syncs the library on successful sign-in.

Three stores use command-line clients that Qtris downloads from their upstream
releases (``qtris.runtime``) and runs through ``qtris.util.store_cli``:
gogdl for GOG, Legendary for the Epic Games Store, and Nile for Amazon Games.
Legendary and Nile keep their sign-in, library and installed games in
``legendary`` and ``nile`` folders under Qtris's
configuration folder. Their games install with the ``legendary_setup`` and
``nile_setup`` directives (download, update or repair) and run on the Wine
runner. At each launch, ``Game.get_store_launch()`` asks the store, on a worker
thread, what the game needs: legendary signs the game in to Epic and gives its
parameters, and Nile gives the Amazon Games SDK settings
(``BaseService.get_store_launch``). Uninstalling tells the client to forget the
game and leaves its files to Qtris (``BaseService.forget_installed_game``). ``services`` also supports manual sync for local sources.
Ubisoft Connect can reuse saved credentials or accept a manual browser handoff:
paste ``PRODloginData`` if it contains the session fields, or a JSON array of
``PRODloginData``, ``PRODrememberMe`` and ``PRODlastProfile`` (``null`` for
missing values). Qtris verifies the session before saving it. Treat these
values like passwords. For sources with a separate game launcher, the preferred
registered launcher is used for both opening the store and its new install plans.
