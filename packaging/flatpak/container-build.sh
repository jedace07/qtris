#!/bin/bash
# Build the Flatpak in the Flathub CI image that CI builds in, with the flags
# flatpak/flatpak-github-actions passes to flatpak-builder, so local builds match
# CI and nothing is installed on the host. From the repository root:
#
#   podman run --rm --privileged --security-opt label=disable \
#       -v qtris-flatpak:/var/lib/flatpak -v "$PWD:/src" -w /src \
#       ghcr.io/flathub-infra/flatpak-github-actions:freedesktop-26.08 \
#       packaging/flatpak/container-build.sh
#
# The qtris-flatpak volume keeps the runtimes between builds, and must be a volume:
# flatpak can't update the runtimes the image ships inside its own overlay
# ("Invalid cross-device link").
#
# Arguments: the manifest (io.github.jedace07.Qtris.yml by default), and a bundle file to
# write once it's built. Output goes under build-flatpak/, which git ignores. Set
# OWNER (uid:gid) to hand that output back to the host user afterwards.
set -euo pipefail

manifest=${1:-packaging/flatpak/io.github.jedace07.Qtris.yml}
bundle=${2:-}

if [ ! -e "$(dirname "$manifest")/shared-modules/README.md" ]; then
    echo "The shared-modules submodule is missing; run: git submodule update --init" >&2
    exit 1
fi

# The checkout and a restored flatpak-builder cache (with its git mirrors) can
# belong to another user, and git won't use such repositories otherwise
git config --global --add safe.directory '*'
flatpak-builder --version

status=0
flatpak-builder --install-deps-from=flathub --disable-rofiles-fuse --force-clean --ccache \
    --state-dir=build-flatpak/state --repo=build-flatpak/repo \
    build-flatpak/build "$manifest" || status=$?

if [ "$status" -eq 0 ] && [ -n "$bundle" ]; then
    flatpak build-bundle build-flatpak/repo "$bundle" io.github.jedace07.Qtris \
        --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo || status=$?
fi

if [ -n "${OWNER:-}" ]; then
    chown -R "$OWNER" build-flatpak ${bundle:+"$bundle"} 2>/dev/null || true
fi
exit "$status"
