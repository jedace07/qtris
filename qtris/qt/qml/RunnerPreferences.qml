pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtrisStyle

ColumnLayout {
    id: page

    required property QtObject controller
    required property QtObject configuration

    spacing: 12
    Component.onCompleted: controller.open()

    RowLayout {
        Layout.fillWidth: true
        visible: page.controller.proton

        Button {
            text: qsTr("Back to Runners")
            icon.source: Theme.icon("arrow-up")
            enabled: !page.controller.busy
            onClicked: page.controller.open()
        }

        Item {
            Layout.fillWidth: true
        }

        Button {
            text: qsTr("Check for Releases")
            icon.source: Theme.icon("refresh")
            enabled: !page.controller.busy
            onClicked: page.controller.show_proton()
        }
    }

    TabBar {
        objectName: "protonFamilies"
        visible: page.controller.proton
        enabled: !page.controller.busy
        currentIndex: page.controller.protonFamily

        Repeater {
            model: page.controller.protonFamilies

            TabButton {
                required property string modelData
                required property int index

                objectName: "protonFamily_" + index
                text: modelData
                width: implicitWidth
                onClicked: page.controller.select_family(index)
            }
        }
    }

    Label {
        Layout.fillWidth: true
        visible: !page.controller.proton
        text: qsTr("Runners that are turned off aren't listed in the sidebar or offered for new games.")
        wrapMode: Text.WordWrap
        font.pointSize: Theme.smallSize
        color: Theme.textMuted
    }

    Label {
        Layout.fillWidth: true
        visible: page.controller.proton
        text: page.controller.protonNote
        wrapMode: Text.WordWrap
        font.pointSize: Theme.smallSize
        color: Theme.textMuted
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.fillHeight: true
        radius: Theme.radiusLarge
        color: Theme.surface
        border.color: Theme.border

        ListView {
            id: entries
            objectName: "runnerCatalog"
            anchors.fill: parent
            anchors.margins: 1
            clip: true
            model: page.controller.entries
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}

            delegate: SettingsRow {
                id: entry

                required property var modelData

                width: ListView.view.width
                title: modelData.label
                subtitle: modelData.description

                // Runners only say when one that's on is missing; Proton builds always say
                Rectangle {
                    visible: !entry.modelData.canToggle || (entry.modelData.enabled && !entry.modelData.installed)
                    implicitWidth: stateLabel.implicitWidth + 16
                    implicitHeight: stateLabel.implicitHeight + 6
                    radius: height / 2
                    color: entry.modelData.installed ? Qt.alpha(Theme.success, 0.15) : Theme.hover

                    Label {
                        id: stateLabel
                        anchors.centerIn: parent
                        text: entry.modelData.installed ? qsTr("Installed") : qsTr("Not installed")
                        font.pointSize: Theme.smallSize * 0.9
                        font.weight: Font.Medium
                        color: entry.modelData.installed ? Theme.success : Theme.textMuted
                    }
                }

                ToolButton {
                    objectName: "runnerConfigure_" + entry.modelData.key
                    visible: entry.modelData.canConfigure
                    enabled: !page.controller.busy
                    icon.source: Theme.icon("configure")
                    Accessible.name: qsTr("Configure %1").arg(entry.modelData.label)
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Configure…")
                    onClicked: page.configuration.open_runner(entry.modelData.key, entry.modelData.label)
                }

                Button {
                    objectName: "runnerVersions_" + entry.modelData.key
                    visible: entry.modelData.manageProton
                    enabled: !page.controller.busy
                    text: qsTr("Manage Proton")
                    onClicked: page.controller.show_proton()
                }

                Button {
                    objectName: "runnerInstall_" + entry.modelData.key
                    visible: entry.modelData.canInstall
                    enabled: !page.controller.busy
                    highlighted: true
                    text: qsTr("Install")
                    icon.source: Theme.icon("download")
                    Accessible.name: qsTr("Install %1").arg(entry.modelData.label)
                    onClicked: page.controller.install(entry.modelData.key)
                }

                Button {
                    objectName: "runnerUpdate_" + entry.modelData.key
                    visible: entry.modelData.canUpdate
                    enabled: !page.controller.busy
                    highlighted: true
                    text: qsTr("Update")
                    icon.source: Theme.icon("download")
                    Accessible.name: qsTr("Update %1").arg(entry.modelData.label)
                    onClicked: page.controller.update(entry.modelData.key)
                }

                ToolButton {
                    objectName: "runnerRemove_" + entry.modelData.key
                    visible: entry.modelData.canRemove
                    enabled: !page.controller.busy
                    icon.source: Theme.icon("trash")
                    icon.color: Theme.danger
                    Accessible.name: qsTr("Remove %1").arg(entry.modelData.label)
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Remove…")
                    onClicked: page.controller.remove(entry.modelData.key)
                }

                Switch {
                    objectName: "runnerSwitch_" + entry.modelData.key
                    visible: entry.modelData.canToggle
                    enabled: !page.controller.busy
                    checked: entry.modelData.enabled
                    Accessible.name: qsTr("Use %1").arg(entry.modelData.label)
                    onToggled: page.controller.set_enabled(entry.modelData.key, checked)
                }
            }

            Label {
                anchors.centerIn: parent
                visible: !page.controller.busy && entries.count === 0
                text: page.controller.proton ? qsTr("No Proton builds found") : qsTr("No runners found")
                color: Theme.textMuted
            }
        }
    }

    Label {
        Layout.fillWidth: true
        visible: text.length > 0
        text: page.controller.message
        wrapMode: Text.WordWrap
        color: Theme.textMuted
    }

    ProgressBar {
        Layout.fillWidth: true
        visible: page.controller.busy
        indeterminate: visible && page.controller.fraction < 0
        value: Math.max(0, page.controller.fraction)
        Accessible.name: page.controller.message
    }
}
