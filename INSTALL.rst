Installing Qtris
=================

There are two supported ways to run Qtris: the Flatpak, built from this
repository, or straight from the source tree with `uv <https://docs.astral.sh/uv/>`_.

Flatpak
-------

The Flatpak uses the Freedesktop runtime and its 32-bit compatibility
extensions. Qtris downloads Proton on demand, and UMU downloads the Steam
Linux Runtime when a Windows game needs it. Store sign-in pages and web games
use the bundled QtWebEngine. The manifest lives in ``packaging/flatpak``.

Build it with the ``org.flatpak.Builder`` app from Flathub. It is the same
``flatpak-builder`` that Flathub and CI use, and it stays current whatever your
distribution ships, so builds behave the same everywhere. Install it once::

    flatpak install flathub org.flatpak.Builder

``flatpak-builder`` only builds the app into a directory; it doesn't produce a
file you can install. ``--repo`` exports the build into a local repository,
and ``flatpak build-bundle`` turns that into a single ``qtris.flatpak`` file::

    git clone --recurse-submodules https://github.com/jedace07/qtris
    cd qtris
    flatpak run org.flatpak.Builder --install-deps-from=flathub --force-clean \
        --state-dir=build-flatpak/state --repo=build-flatpak/repo \
        build-flatpak/build packaging/flatpak/io.github.jedace07.Qtris.yml
    flatpak build-bundle build-flatpak/repo qtris.flatpak io.github.jedace07.Qtris \
        --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo

Install the bundle like any other Flatpak, system-wide or with ``--user``, and
run it::

    flatpak install qtris.flatpak
    flatpak run io.github.jedace07.Qtris

The manifest includes modules from Flathub's ``shared-modules`` git submodule.
In a checkout cloned without ``--recurse-submodules``, run
``git submodule update --init`` first, or the build fails to load
``shared-modules/glew/glew.json``.

Rebuilding reuses the cache in ``build-flatpak/state``, so only changed modules
build again.

The build needs no network access beyond downloading its sources. The Python
dependencies come from ``packaging/flatpak/python-dependencies.json``, which
pins what ``uv.lock`` does; after changing ``uv.lock``, regenerate it with
``uv run --no-project packaging/flatpak/update-python-dependencies.py``.

Every CI build of ``main`` publishes a bundle, and every release has one
attached.

Install the optional MangoHud overlay or gamescope extension if you use it.
Qtris picks them up the next time it starts::

    flatpak install flathub org.freedesktop.Platform.VulkanLayer.MangoHud//26.08
    flatpak install flathub org.freedesktop.Platform.VulkanLayer.gamescope//26.08

Install the extension in the same Flatpak installation as Qtris. Pass
``--user`` if you installed Qtris per user.

The sandbox can read and write your home directory and any drive mounted under
``/mnt``, ``/media`` or ``/run/media``. Games elsewhere need an override, for
example ``flatpak override --user --filesystem=/srv/games io.github.jedace07.Qtris``.

From source
-----------

uv installs Qtris and the Python dependencies pinned in ``uv.lock`` in a
virtualenv in the checkout. The rest comes from your distribution.

Build requirements, for the Python packages that compile against C libraries
(PyGObject, pycairo, dbus-python and evdev):

* Fedora: ``gcc pkgconf-pkg-config gobject-introspection-devel cairo-devel cairo-gobject-devel dbus-devel kernel-headers``
* Debian/Ubuntu: ``build-essential pkg-config libgirepository-2.0-dev libcairo2-dev libdbus-1-dev linux-libc-dev``
* Arch: ``base-devel gobject-introspection cairo dbus linux-api-headers``

Runtime requirements, the libraries Qtris loads through GObject introspection
and the tools it runs. The desktop application uses Qt Quick and QtWebEngine
(PySide6 from ``uv.lock``); it does not require GTK introspection packages:

* Fedora: ``glib-networking file-libs psmisc cabextract unzip 7zip curl xrandr mesa-demos vulkan-tools``
* Debian/Ubuntu: ``glib-networking libmagic1 psmisc cabextract unzip p7zip-full curl x11-xserver-utils mesa-utils vulkan-tools``
* Arch: ``glib-networking file psmisc cabextract unzip p7zip curl xorg-xrandr mesa-utils vulkan-tools``

QtWebEngine, which shows the stores' sign-in pages and runs web games, also
loads these libraries.
Qtris doesn't start without them, though most desktops already have them:

* Fedora: ``nss nspr libxkbfile libXcomposite libXdamage libXfixes libXrandr libXtst alsa-lib krb5-libs libglvnd-glx``
* Debian/Ubuntu: ``libgl1 libnss3 libnspr4 libxkbfile1 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libxtst6 libasound2t64 libgssapi-krb5-2``
* Arch: ``nss nspr libxkbfile libxcomposite libxdamage libxfixes libxrandr libxtst alsa-lib krb5 libglvnd``

On X11, Qt's ``xcb`` plugin also needs your distribution's XCB helper libraries,
including ``xcb-util-cursor`` (``libxcb-cursor0`` on Debian/Ubuntu).
To run games you'll also want the 32-bit OpenGL and Vulkan drivers for your
graphics card, and optionally Wine and gamescope. Bundled or separately
installed tools and games may still use GTK themselves.

Install GNU gettext if you want translations. After installing the system
dependencies, run::

    git clone https://github.com/jedace07/qtris
    cd qtris
    uv run qtris

uv downloads its own build of Python 3.14 for this; your system's Python isn't
used, so it doesn't matter which version (if any) your distribution ships.
After pulling new changes, ``uv run`` updates the environment. Use
``uv run --no-dev qtris`` to skip the development tools or ``uv build`` to
build a wheel and source distribution. The checkout's virtualenv does not
register Qtris in your desktop menu; use the Flatpak for desktop integration.
