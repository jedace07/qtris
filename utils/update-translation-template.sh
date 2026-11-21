#!/bin/sh
set -eu

cd "$(dirname "$0")/.."
desktop=packaging/desktop/share
template=qtris/locale/qtris.pot

find qtris -name '*.py' | LC_ALL=C sort | xgettext --from-code=UTF-8 --keyword=_ --package-name=Qtris \
    --files-from=- --output="$template" \
    "$desktop/applications/io.github.jedace07.Qtris.desktop" "$desktop/metainfo/io.github.jedace07.Qtris.metainfo.xml"
find qtris/qt/qml -name '*.qml' | LC_ALL=C sort | xgettext --from-code=UTF-8 --language=JavaScript --keyword=qsTr \
    --join-existing --files-from=- --output="$template"
