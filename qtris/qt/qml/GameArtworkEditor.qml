pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs as NativeDialogs
import QtQuick.Layouts
import QtrisBridge
import QtrisStyle

// A game's cover, banner and icon, each of which can be replaced or reset
ColumnLayout {
    id: editor
    objectName: "gameArtworkEditor"

    required property GameArtworkController artwork

    spacing: 8

    NativeDialogs.FileDialog {
        id: picker

        property string kind: ""

        title: qsTr("Choose an image")
        nameFilters: [qsTr("Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff)"), qsTr("All files (*)")]
        onAccepted: editor.artwork.choose_file(kind, selectedFile)
    }

    Label {
        text: qsTr("Artwork")
        font.weight: Font.DemiBold
    }

    Flow {
        Layout.fillWidth: true
        spacing: 16

        Repeater {
            model: editor.artwork.images

            ColumnLayout {
                id: slot

                required property var modelData
                readonly property size frame: modelData.kind === "cover" ? Qt.size(120, 180) : modelData.kind === "banner" ? Qt.size(230, 107) : Qt.size(96, 96)

                spacing: 4

                Label {
                    text: slot.modelData.custom ? qsTr("%1 (custom)").arg(slot.modelData.label) : slot.modelData.label
                    font.pointSize: Theme.smallSize
                    color: Theme.textMuted
                }

                Rectangle {
                    implicitWidth: slot.frame.width
                    implicitHeight: slot.frame.height
                    radius: Theme.radius
                    color: Theme.surface
                    border.color: Theme.border

                    Image {
                        objectName: "artworkImage_" + slot.modelData.kind
                        anchors.fill: parent
                        anchors.margins: 6
                        source: slot.modelData.url
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                        cache: false
                    }

                    Icon {
                        anchors.centerIn: parent
                        visible: slot.modelData.url.toString().length === 0
                        symbol: "image"
                        size: 28
                        tint: Theme.textSubtle
                    }

                    BusyIndicator {
                        anchors.centerIn: parent
                        running: slot.modelData.working
                        visible: running
                    }
                }

                Button {
                    Layout.fillWidth: true
                    text: qsTr("Choose File…")
                    icon.source: Theme.icon("folder")
                    enabled: !editor.artwork.working
                    Accessible.name: qsTr("Choose a file for the %1").arg(slot.modelData.label)
                    onClicked: {
                        picker.kind = slot.modelData.kind;
                        picker.open();
                    }
                }

                Button {
                    objectName: "steamGridDbArtwork_" + slot.modelData.kind
                    Layout.fillWidth: true
                    text: qsTr("SteamGridDB…")
                    icon.source: Theme.icon("image")
                    enabled: !editor.artwork.working && !editor.artwork.loadingChoices
                    Accessible.name: qsTr("Choose the %1 from SteamGridDB").arg(slot.modelData.label)
                    onClicked: {
                        editor.artwork.load_choices(slot.modelData.kind);
                        chooser.open();
                    }
                }

                Button {
                    objectName: "resetArtwork_" + slot.modelData.kind
                    Layout.fillWidth: true
                    flat: true
                    text: qsTr("Reset")
                    icon.source: Theme.icon("refresh")
                    enabled: !editor.artwork.working
                    Accessible.name: qsTr("Reset the %1").arg(slot.modelData.label)
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Use the artwork from the game's store or SteamGridDB again")
                    onClicked: editor.artwork.reset(slot.modelData.kind)
                }
            }
        }
    }

    Dialog {
        id: chooser
        objectName: "steamGridDbChooser"

        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(760, parent.width - 32)
        height: Math.min(560, parent.height - 32)
        modal: true
        title: qsTr("Choose from SteamGridDB")
        onOpened: search.text = ""
        standardButtons: Dialog.Close

        contentItem: ColumnLayout {
            RowLayout {
                Layout.fillWidth: true

                Label {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    text: editor.artwork.steamGridDbMatch ? qsTr("Matched to %1").arg(editor.artwork.steamGridDbMatch) : qsTr("Automatic game match")
                }

                Button {
                    text: qsTr("Use Automatic Match")
                    visible: editor.artwork.steamGridDbMatch !== ""
                    enabled: !editor.artwork.loadingChoices
                    onClicked: editor.artwork.reset_match()
                }
            }

            RowLayout {
                Layout.fillWidth: true

                TextField {
                    id: search
                    objectName: "steamGridDbSearch"
                    Layout.fillWidth: true
                    placeholderText: qsTr("Search for another game")
                    Accessible.name: qsTr("SteamGridDB game title")
                    onAccepted: if (searchButton.enabled)
                        searchButton.clicked()
                }

                Button {
                    id: searchButton
                    text: qsTr("Search")
                    enabled: search.text.trim() !== "" && !editor.artwork.searching && !editor.artwork.loadingChoices
                    onClicked: editor.artwork.search_matches(search.text)
                }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: matches.count > 0

                ComboBox {
                    id: matches
                    objectName: "steamGridDbMatches"
                    Layout.fillWidth: true
                    model: editor.artwork.steamGridDbMatches
                    Accessible.name: qsTr("Matching games")
                }

                Button {
                    objectName: "steamGridDbSelectMatch"
                    text: qsTr("Use This Game")
                    enabled: matches.currentIndex >= 0 && !editor.artwork.loadingChoices
                    onClicked: editor.artwork.select_match(matches.currentIndex)
                }
            }

            Label {
                Layout.fillWidth: true
                visible: editor.artwork.searched && matches.count === 0
                text: qsTr("No games matched your search.")
            }

            ProgressBar {
                Layout.fillWidth: true
                visible: editor.artwork.loadingChoices || editor.artwork.searching
                indeterminate: true
                Accessible.name: qsTr("Searching SteamGridDB…")
            }

            Label {
                Layout.fillWidth: true
                Layout.fillHeight: true
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                visible: !editor.artwork.loadingChoices && grid.count === 0
                text: qsTr("No artwork found. Try another game match.")
                wrapMode: Text.WordWrap
            }

            GridView {
                id: grid
                visible: count > 0 || editor.artwork.loadingChoices
                objectName: "steamGridDbChoices"
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: editor.artwork.choices
                cellWidth: editor.artwork.choicesKind === "banner" ? 240 : editor.artwork.choicesKind === "icon" ? 112 : 136
                cellHeight: editor.artwork.choicesKind === "banner" ? 128 : editor.artwork.choicesKind === "icon" ? 112 : 212
                ScrollBar.vertical: ScrollBar {}

                delegate: ItemDelegate {
                    id: choice

                    required property var modelData
                    required property int index

                    objectName: "steamGridDbChoice" + index
                    width: grid.cellWidth - 8
                    height: grid.cellHeight - 8
                    Accessible.name: qsTr("%1 × %2 image").arg(modelData.width).arg(modelData.height)
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("%1 × %2").arg(modelData.width).arg(modelData.height)

                    contentItem: Image {
                        source: choice.modelData.thumbnail
                        fillMode: Image.PreserveAspectFit
                        asynchronous: true
                    }

                    onClicked: {
                        editor.artwork.choose_url(modelData.url);
                        chooser.close();
                    }
                }
            }
        }
    }
}
