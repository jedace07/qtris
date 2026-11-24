pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs as NativeDialogs
import QtQuick.Layouts
import QtrisBridge
import QtrisStyle

SettingsGroup {
    id: storage

    required property PreferencesController controller

    NativeDialogs.FolderDialog {
        id: folderPicker

        property bool choosingPrefix: false

        onAccepted: {
            const path = storage.controller.local_path(selectedFolder);
            if (choosingPrefix)
                storage.controller.set_prefix_path(path);
            else
                storage.controller.set_game_path(path);
        }
    }

    PathRow {
        title: qsTr("Game library")
        subtitle: qsTr("Default location for new game installations.")
        path: storage.controller.storage.gamePath ?? ""
        onPathEdited: path => storage.controller.set_game_path(path)
    }

    PathRow {
        title: qsTr("Windows game prefixes")
        subtitle: qsTr("Where Qtris creates a separate Windows environment for each new game.")
        path: storage.controller.storage.prefixPath ?? ""
        isPrefix: true
        onPathEdited: path => storage.controller.set_prefix_path(path)
    }

    component PathRow: SettingsRow {
        id: pathRow

        required property string path
        property bool isPrefix: false

        signal pathEdited(string path)

        details: [
            RowLayout {
                Layout.fillWidth: true

                TextField {
                    objectName: pathRow.isPrefix ? "storagePath_prefix" : "storagePath_game"
                    Layout.fillWidth: true
                    text: pathRow.path
                    Accessible.name: pathRow.title
                    onEditingFinished: {
                        if (text !== pathRow.path)
                            pathRow.pathEdited(text);
                    }
                }

                Button {
                    text: qsTr("Browse…")
                    icon.source: Theme.icon("folder")
                    Accessible.name: pathRow.isPrefix ? qsTr("Browse for the Windows prefixes folder") : qsTr("Browse for the game library folder")
                    onClicked: {
                        folderPicker.choosingPrefix = pathRow.isPrefix;
                        folderPicker.title = pathRow.title;
                        folderPicker.currentFolder = storage.controller.folder_url(pathRow.path);
                        folderPicker.open();
                    }
                }
            },
            Label {
                Layout.fillWidth: true
                Layout.topMargin: 4
                visible: pathRow.isPrefix
                text: qsTr("Folder changes apply to new installations.")
                wrapMode: Text.WordWrap
                font.pointSize: Theme.smallSize
                color: Theme.textMuted
            }
        ]
    }
}
