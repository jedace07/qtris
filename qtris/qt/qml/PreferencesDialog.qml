pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtrisBridge
import QtrisStyle

Dialog {
    id: dialog
    objectName: "preferencesDialog"

    required property PreferencesController controller
    required property LibraryController library

    readonly property var pageList: [
        { title: qsTr("Stores"), icon: "store" },
        { title: qsTr("Storage"), icon: "storage" },
        { title: qsTr("Updates"), icon: "refresh" },
        { title: qsTr("Accounts"), icon: "user" },
        { title: qsTr("System"), icon: "info" },
        { title: qsTr("Options"), icon: "configure" },
        { title: qsTr("Local Games"), icon: "platform" },
        { title: qsTr("Interface"), icon: "layout" }
    ]

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(960, parent.width - 48)
    height: Math.min(680, parent.height - 48)
    title: qsTr("Preferences")
    modal: true
    padding: 0
    topPadding: 0
    bottomPadding: 0
    header: null
    footer: null

    // A page's scrolling column, under the page's title
    component Page: ScrollView {
        id: page

        default property alias content: column.data

        contentWidth: availableWidth
        rightPadding: 8

        ColumnLayout {
            id: column
            width: page.availableWidth
            spacing: 22
        }
    }

    contentItem: RowLayout {
        spacing: 0

        Rectangle {
            Layout.preferredWidth: 220
            Layout.fillHeight: true
            topLeftRadius: 14
            bottomLeftRadius: 14
            color: Theme.sidebar

            Rectangle {
                anchors.right: parent.right
                width: 1
                height: parent.height
                color: Theme.border
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 12
                anchors.topMargin: 20
                spacing: 12

                Label {
                    Layout.leftMargin: 8
                    text: dialog.title
                    font.pointSize: Theme.titleSize
                    font.weight: Font.Bold
                }

                ListView {
                    id: pages
                    objectName: "preferencesPages"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    model: dialog.pageList
                    currentIndex: 0
                    spacing: 1
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds

                    delegate: ItemDelegate {
                        required property var modelData
                        required property int index

                        width: ListView.view.width
                        text: modelData.title
                        icon.source: Theme.icon(modelData.icon)
                        highlighted: ListView.isCurrentItem
                        font.weight: highlighted ? Font.DemiBold : Font.Normal
                        onClicked: pages.currentIndex = index
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 28
                Layout.rightMargin: 12
                Layout.topMargin: 12
                Layout.bottomMargin: 8

                Label {
                    Layout.fillWidth: true
                    text: dialog.pageList[pages.currentIndex]?.title ?? ""
                    font.pointSize: Theme.headingSize * 0.8
                    font.weight: Font.Bold
                    elide: Text.ElideRight
                }

                ToolButton {
                    icon.source: Theme.icon("close")
                    Accessible.name: qsTr("Close")
                    onClicked: dialog.close()
                }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.leftMargin: 28
                Layout.rightMargin: 20
                Layout.bottomMargin: 20
                currentIndex: pages.currentIndex

                // Stores
                Page {
                    SettingsGroup {
                        description: qsTr("Choose which stores appear in the sidebar.")

                        Repeater {
                            model: dialog.controller.sources

                            SettingsRow {
                                id: source

                                required property var modelData

                                title: modelData.name
                                subtitle: modelData.description

                                Switch {
                                    objectName: "sourceSwitch_" + source.modelData.id
                                    checked: source.modelData.enabled
                                    Accessible.name: qsTr("Use %1").arg(source.modelData.name)
                                    onToggled: dialog.controller.set_source_enabled(source.modelData.id, checked)
                                }

                                details: CheckBox {
                                    visible: source.modelData.enabled
                                    checked: source.modelData.inGamesView
                                    text: qsTr("Show games from this store in the Games view")
                                    font.pointSize: Theme.smallSize
                                    onToggled: dialog.controller.set_source_in_games_view(source.modelData.id, checked)
                                }
                            }
                        }
                    }
                }

                // Storage
                Page {
                    StoragePreferences {
                        controller: dialog.controller
                    }
                }

                // Updates
                Page {
                    UpdatePreferences {
                        runtime: dialog.library.runtimeUpdates
                        library: dialog.library
                    }
                }

                // Accounts
                Page {
                    SettingsGroup {
                        title: qsTr("Steam account")
                        description: qsTr("Select which Steam account is used for Steam integration and Steam shortcuts.")

                        ButtonGroup { id: accounts }

                        Repeater {
                            model: dialog.controller.steamAccounts

                            SettingsRow {
                                id: account

                                required property var modelData

                                title: modelData.name

                                RadioButton {
                                    Accessible.name: account.modelData.name
                                    checked: account.modelData.active
                                    ButtonGroup.group: accounts
                                    onToggled: if (checked) dialog.controller.set_steam_account(account.modelData.id)
                                }
                            }
                        }

                        SettingsRow {
                            visible: dialog.controller.steamAccounts.length === 0
                            title: qsTr("No Steam account found")
                            subtitle: qsTr("Sign in to Steam on this computer to use its account here.")
                        }
                    }
                }

                // System
                Page {
                    RowLayout {
                        Layout.fillWidth: true

                        Button {
                            text: qsTr("Copy System Info")
                            icon.source: Theme.icon("info")
                            onClicked: dialog.controller.copy_system_info()
                        }

                        Button {
                            text: qsTr("Copy Qtris Log")
                            icon.source: Theme.icon("terminal")
                            onClicked: dialog.controller.copy_log()
                        }
                    }

                    Repeater {
                        model: dialog.controller.systemInfo

                        SettingsGroup {
                            id: section

                            required property var modelData

                            title: modelData.title

                            Repeater {
                                model: section.modelData.items

                                SettingsRow {
                                    id: item

                                    required property var modelData

                                    topPadding: 8
                                    bottomPadding: 8

                                    details: RowLayout {
                                        Layout.fillWidth: true
                                        spacing: 16

                                        Label {
                                            Layout.preferredWidth: 220
                                            Layout.alignment: Qt.AlignTop
                                            text: item.modelData.name
                                            textFormat: Text.PlainText
                                            color: Theme.textMuted
                                            wrapMode: Text.Wrap
                                        }

                                        Label {
                                            Layout.fillWidth: true
                                            text: item.modelData.value
                                            textFormat: Text.PlainText
                                            wrapMode: Text.Wrap
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Options
                Page {
                    SettingsGroup {
                        SettingsRow {
                            title: qsTr("Global options")
                            subtitle: qsTr("These are the default system options for all games.")

                            Button {
                                text: qsTr("Edit Global Options…")
                                icon.source: Theme.icon("configure")
                                onClicked: dialog.library.configuration.open_system()
                            }
                        }
                    }
                }

                Loader {
                    active: visible
                    sourceComponent: RunnerPreferences {
                        controller: dialog.library.runnerManager
                        configuration: dialog.library.configuration
                    }
                }

                InterfacePreferences { controller: dialog.controller }
            }
        }
    }
}
