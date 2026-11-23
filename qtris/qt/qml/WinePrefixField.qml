pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs as NativeDialogs
import QtQuick.Layouts
import QtrisBridge
import QtrisStyle

ColumnLayout {
    id: field

    required property InstallerController controller
    property alias text: path.text
    property string label: qsTr("Wine prefix (optional)")

    spacing: 8

    NativeDialogs.FolderDialog {
        id: picker
        title: qsTr("Choose a Wine prefix folder")
        onAccepted: field.text = field.controller.local_path(selectedFolder)
    }

    Label {
        text: field.label
        font.weight: Font.DemiBold
    }

    RowLayout {
        Layout.fillWidth: true

        TextField {
            id: path
            Layout.fillWidth: true
            placeholderText: qsTr("Use the default prefix")
            Accessible.name: field.label
        }

        Button {
            text: qsTr("Browse…")
            icon.source: Theme.icon("folder")
            Accessible.name: qsTr("Browse for a Wine prefix")
            onClicked: picker.open()
        }
    }
}
