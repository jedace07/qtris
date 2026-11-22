pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs as NativeDialogs
import QtQuick.Layouts
import QtrisStyle

ColumnLayout {
    id: form

    required property QtObject controller
    readonly property bool ready: !controller.importing && folder.text.trim().length > 0

    spacing: 8
    enabled: !controller.importing
    onVisibleChanged: if (visible)
        folder.forceActiveFocus()

    function submit() {
        controller.import_existing(folder.text, executable.text)
    }

    function clear() {
        folder.clear()
        executable.clear()
    }

    NativeDialogs.FolderDialog {
        id: folderPicker
        title: qsTr("Choose the game folder")
        onAccepted: folder.text = form.controller.local_path(selectedFolder)
    }

    NativeDialogs.FileDialog {
        id: executablePicker
        title: qsTr("Choose the game executable")
        onAccepted: executable.text = form.controller.local_path(selectedFile)
    }

    Label {
        Layout.fillWidth: true
        text: qsTr("Choose the folder containing the game. Qtris will link to the files on that drive.")
        color: Theme.textMuted
        wrapMode: Text.WordWrap
    }

    Label {
        text: qsTr("Game folder")
        font.weight: Font.DemiBold
    }

    RowLayout {
        Layout.fillWidth: true

        TextField {
            id: folder
            objectName: "existingGameFolder"
            Layout.fillWidth: true
            Accessible.name: qsTr("Game folder")
        }

        Button {
            text: qsTr("Browse…")
            icon.source: Theme.icon("folder")
            Accessible.name: qsTr("Browse for game folder")
            onClicked: folderPicker.open()
        }
    }

    Label {
        text: qsTr("Game executable")
        font.weight: Font.DemiBold
    }

    RowLayout {
        Layout.fillWidth: true

        TextField {
            id: executable
            objectName: "existingGameExecutable"
            Layout.fillWidth: true
            placeholderText: qsTr("Detect from GOG metadata")
            Accessible.name: qsTr("Game executable")
        }

        Button {
            text: qsTr("Browse…")
            icon.source: Theme.icon("folder")
            enabled: folder.text.trim().length > 0
            Accessible.name: qsTr("Browse for game executable")
            onClicked: {
                executablePicker.currentFolder = form.controller.folder_url(folder.text.trim())
                executablePicker.open()
            }
        }
    }

    Label {
        objectName: "existingGameError"
        Layout.fillWidth: true
        visible: text.length > 0
        text: form.controller.importError
        textFormat: Text.PlainText
        color: Theme.danger
        wrapMode: Text.WordWrap
    }

    ProgressBar {
        Layout.fillWidth: true
        visible: form.controller.importing
        indeterminate: visible
        Accessible.name: qsTr("Linking game…")
    }

    Item {
        Layout.fillHeight: true
    }
}
