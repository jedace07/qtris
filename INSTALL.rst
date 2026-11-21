Installing Qtris
=================

There are two supported ways to run Qtris: the Flatpak, built from this
repository, or straight from the source tree with `uv <https://docs.astral.sh/uv/>`_.

Flatpak
-------

The Flatpak bundles everything Qtris needs, including the 32-bit libraries,
tools and Wine base that games rely on, on the freedesktop runtime. Its
manifest lives in ``packaging/flatpak``.

Build it with the ``org.flatpak.Builder`` app from Flathub, the same
``flatpak-builder`` that Flathub and CI use (distribution packages can be too
old)::

    git clone --recurse-submodules https://github.com/jedace07/qtris
    cd qtris
    flatpak install --user flathub org.flatpak.Builder
    flatpak run org.flatpak.Builder --user --install --install-deps-from=flathub --force-clean \
        --state-dir=build-flatpak/state build-flatpak/build packaging/flatpak/io.github.jedace07.Qtris.yml
    flatpak run io.github.jedace07.Qtris

The manifest includes modules from Flathub's ``shared-modules`` git submodule.
In a checkout cloned without ``--recurse-submodules``, run
``git submodule update --init`` first, or the build fails to load
``shared-modules/glew/glew.json``.

To make a single-file bundle instead of installing, replace ``--install`` with
``--repo=build-flatpak/repo``, then run
``flatpak build-bundle build-flatpak/repo qtris.flatpak io.github.jedace07.Qtris``.

The build needs no network access beyond downloading its sources. The Python
dependencies come from ``packaging/flatpak/python-dependencies.json``, which
pins what ``uv.lock`` does; after changing ``uv.lock``, regenerate it with
``uv run --no-project packaging/flatpak/update-python-dependencies.py``.

Every CI build of ``main`` publishes a bundle, and every release has one
attached. The first build compiles bundled tools such as Mono. Later builds
reuse flatpak-builder's cache.

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
and the tools it runs. The desktop application uses Qt Quick (PySide6 Essentials
from ``uv.lock``); it does not require GTK introspection packages:

* Fedora: ``glib-networking file-libs psmisc cabextract unzip 7zip curl xrandr mesa-demos vulkan-tools fluid-soundfont-gs``
* Debian/Ubuntu: ``glib-networking libmagic1 psmisc cabextract unzip p7zip-full curl x11-xserver-utils mesa-utils vulkan-tools fluid-soundfont-gs``
* Arch: ``glib-networking file psmisc cabextract unzip p7zip curl xorg-xrandr mesa-utils vulkan-tools soundfont-fluid``

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
