pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtrisBridge
import QtrisStyle

ColumnLayout {
    id: updates

    required property RuntimeUpdatesController runtime
    required property LibraryController library

    Layout.fillWidth: true
    spacing: 22

    SettingsGroup {
        title: qsTr("Compatibility tools")
        description: qsTr("Keep the tools Qtris uses to install and run games up to date.")

        SettingsRow {
            title: qsTr("Update tools automatically")

            Switch {
                objectName: "autoUpdateRuntime"
                checked: updates.runtime.autoUpdate
                Accessible.name: qsTr("Update tools automatically")
                onToggled: updates.runtime.set_auto_update(checked)
            }
        }

        SettingsRow {
            title: qsTr("Check for updates")
            subtitle: updates.runtime.statusText || qsTr("Look for and install newer versions.")

            BusyIndicator {
                implicitWidth: 26
                implicitHeight: 26
                padding: 0
                visible: running
                running: updates.visible && updates.runtime.running
            }

            Button {
                objectName: "checkRuntimeUpdates"
                text: qsTr("Check")
                icon.source: Theme.icon("refresh")
                Accessible.name: qsTr("Check for updates")
                enabled: !updates.runtime.running
                onClicked: updates.runtime.check()
            }
        }

        SettingsRow {
            title: qsTr("Installed versions")
            subtitle: qsTr("Version details for tools installed by Qtris.")

            Button {
                id: showVersions
                objectName: "showRuntimeVersions"
                text: checked ? qsTr("Hide versions") : qsTr("Show versions")
                checkable: true
                onToggled: {
                    if (checked)
                        updates.runtime.refresh_versions();
                }
            }

            details: ColumnLayout {
                Layout.fillWidth: true
                visible: showVersions.checked
                spacing: 8

                Label {
                    Layout.fillWidth: true
                    visible: updates.runtime.versions.length === 0
                    text: qsTr("No compatibility tools have been installed by Qtris yet.")
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }

                Repeater {
                    model: showVersions.checked ? updates.runtime.versions : []

                    RowLayout {
                        id: toolVersion

                        required property var modelData

                        Layout.fillWidth: true
                        spacing: 12

                        Label {
                            Layout.fillWidth: true
                            text: toolVersion.modelData.name
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                        }

                        Label {
                            Layout.fillWidth: true
                            text: toolVersion.modelData.version || qsTr("Version unavailable")
                            textFormat: Text.PlainText
                            color: Theme.textMuted
                            horizontalAlignment: Text.AlignRight
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }
        }
    }

    SettingsGroup {
        title: qsTr("Artwork")

        SettingsRow {
            title: qsTr("Artwork sources")
            subtitle: qsTr("Choose where artwork comes from, such as SteamGridDB.")

            Button {
                text: qsTr("Artwork Sources…")
                onClicked: updates.library.artworkSettings.open_settings()
            }
        }
    }
}
