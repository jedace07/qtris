pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtrisBridge

Dialog {
    id: dialog
    objectName: "launchConfigurationDialog"

    required property PromptController prompts
    property list<string> configurationNames
    property int selectedIndex: 0
    property bool remembered: false

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(460, parent.width - 32)
    modal: true
    standardButtons: Dialog.Ok | Dialog.Cancel
    onOpened: {
        standardButton(Dialog.Ok).text = qsTr("Play");
        configuration.forceActiveFocus();
    }
    onAccepted: prompts.answer_configuration(configuration.currentIndex, remember.checked)
    onRejected: prompts.answer(false, "")

    contentItem: ColumnLayout {
        Label {
            text: qsTr("Choose a launch option")
        }

        ListView {
            id: configuration
            objectName: "launchConfigurationChoice"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(count * 36, 216)
            Accessible.name: qsTr("Launch options")
            activeFocusOnTab: true
            clip: true
            keyNavigationEnabled: true
            model: dialog.configurationNames
            currentIndex: dialog.selectedIndex
            Keys.onReturnPressed: dialog.accept()
            Keys.onSpacePressed: dialog.accept()
            ScrollBar.vertical: ScrollBar {}

            delegate: RadioButton {
                required property int index
                required property string modelData

                width: configuration.width
                height: 36
                text: modelData
                checked: index === configuration.currentIndex
                onClicked: configuration.currentIndex = index
            }
        }

        CheckBox {
            id: remember
            objectName: "rememberLaunchConfiguration"
            text: qsTr("Remember this choice")
            checked: dialog.remembered
        }
    }
}
