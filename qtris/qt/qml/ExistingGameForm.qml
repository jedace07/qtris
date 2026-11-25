pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs as NativeDialogs
import QtQuick.Layouts
import QtrisBridge
import QtrisStyle

ColumnLayout {
    id: form

    required property InstallerController controller
    readonly property bool ready: !controller.importing && folder.text.trim().length > 0

    spacing: 8
    enabled: !controller.importing
    onVisibleChanged: if (visible)
        folder.forceActiveFocus()

    function submit() {
        controller.import_existing(folder.text, customPrefix.checked ? prefix.text : "");
    }

    function clear() {
        folder.clear();
        customPrefix.checked = false;
        prefix.text = "";
    }

    NativeDialogs.FolderDialog {
        id: folderPicker
        title: qsTr("Choose the game folder")
        onAccepted: folder.text = form.controller.local_path(selectedFolder)
    }

    Label {
        Layout.fillWidth: true
        text: qsTr("Choose the folder where the game is installed. Qtris will detect how to launch it and leave the files in place.")
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

    CheckBox {
        id: customPrefix
        objectName: "useExistingWinePrefix"
        text: qsTr("Use a custom Wine prefix for a Windows game")
    }

    WinePrefixField {
        id: prefix
        objectName: "existingWinePrefix"
        Layout.fillWidth: true
        controller: form.controller
        label: qsTr("Wine prefix (Windows games)")
        defaultPath: form.controller.existingWinePrefix
        visible: customPrefix.checked
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
        Accessible.name: qsTr("Importing game…")
    }
}
