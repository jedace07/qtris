pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs as NativeDialogs
import QtQuick.Layouts
import QtrisBridge
import QtrisStyle

Dialog {
    id: dialog
    objectName: "installerDialog"

    required property InstallerController controller
    readonly property InstallationController installation: controller.installation

    property bool usingExisting: false

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(620, parent.width - 48)
    height: Math.min(parent.height - 32, installation.active ? 560 : Math.max(implicitHeaderHeight + implicitFooterHeight + topPadding + bottomPadding + spacing + (usingExisting ? existingForm.implicitHeight : installForm.implicitHeight), 220))
    title: usingExisting ? qsTr("Locate %1").arg(controller.gameName) : controller.title
    modal: true
    closePolicy: installation.running || controller.importing ? Popup.NoAutoClose : Popup.CloseOnEscape

    Connections {
        target: dialog.controller
        function onCloseRequested() {
            dialog.close()
        }
        function onRequested() {
            dialog.usingExisting = false
            existingForm.clear()
            installPrefix.text = ""
        }
    }

    NativeDialogs.FolderDialog {
        id: picker
        title: qsTr("Choose Installation Folder")
        onAccepted: dialog.controller.choose_directory(selectedFolder)
    }

    Dialog {
        id: cancelConfirmation
        objectName: "cancelInstallationDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true
        title: qsTr("Cancel installation?")
        standardButtons: Dialog.Yes | Dialog.No

        ColumnLayout {
            Label {
                text: qsTr("Are you sure you want to cancel the installation?")
            }

            CheckBox {
                id: removeFiles
                objectName: "removeInstallationFiles"
                visible: dialog.installation.canRemoveFiles
                checked: true
                text: qsTr("Remove game files")
            }
        }

        onAccepted: dialog.installation.cancel(removeFiles.checked && dialog.installation.canRemoveFiles)
    }

    contentItem: StackLayout {
        currentIndex: dialog.installation.active ? 2 : dialog.usingExisting ? 1 : 0

        ScrollView {
            id: installScroll
            clip: true
            contentWidth: availableWidth
            contentHeight: installForm.implicitHeight

            ColumnLayout {
                id: installForm
                width: installScroll.availableWidth

                readonly property var choices: dialog.controller.choices
                readonly property var choice: choices[dialog.controller.choiceIndex] ?? null

                spacing: 8

                Label {
                    visible: installForm.choices.length > 1
                    text: qsTr("Installation option")
                    font.weight: Font.DemiBold
                }

                ComboBox {
                    objectName: "installerOptions"
                    Layout.fillWidth: true
                    visible: installForm.choices.length > 1
                    enabled: !dialog.controller.loading
                    model: installForm.choices
                    textRole: "version"
                    currentIndex: dialog.controller.choiceIndex
                    Accessible.name: qsTr("Installation option")
                    onActivated: index => dialog.controller.select(index)
                }

                Label {
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: installForm.choices.length === 1 && !dialog.controller.createsShortcuts ? installForm.choices[0].version : ""
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    font.weight: Font.DemiBold
                }

                Label {
                    objectName: "installerSummary"
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: dialog.controller.requiredGame.length > 0 ? qsTr("Install %1 first.").arg(dialog.controller.requiredGame) : (installForm.choice?.description ?? "")
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    color: Theme.textMuted
                }

                Label {
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: installForm.choice?.notes ?? ""
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    color: Theme.textMuted
                }

                Label {
                    Layout.topMargin: 4
                    visible: dialog.controller.createsGameFolder
                    text: qsTr("Installation folder")
                    font.weight: Font.DemiBold
                }

                RowLayout {
                    Layout.fillWidth: true
                    visible: dialog.controller.createsGameFolder

                    TextField {
                        objectName: "installerDirectory"
                        Layout.fillWidth: true
                        text: dialog.controller.directory
                        Accessible.name: qsTr("Installation folder")
                        onTextEdited: dialog.controller.set_directory(text)
                    }

                    Button {
                        text: qsTr("Browse…")
                        icon.source: Theme.icon("folder")
                        Accessible.name: qsTr("Browse for installation folder")
                        onClicked: picker.open()
                    }
                }

                WinePrefixField {
                    id: installPrefix
                    objectName: "installationWinePrefix"
                    Layout.fillWidth: true
                    controller: dialog.controller
                    visible: dialog.controller.canChooseWinePrefix
                }

                Label {
                    Layout.fillWidth: true
                    visible: dialog.controller.runnersToInstall.length > 0
                    text: qsTr("Qtris will install these components first: %1").arg(dialog.controller.runnersToInstall.join(", "))
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                }

                CheckBox {
                    id: bonusContent
                    visible: dialog.controller.offersExtras
                    text: qsTr("Choose bonus downloads")
                }

                GridLayout {
                    Layout.fillWidth: true
                    visible: dialog.controller.selected && dialog.controller.createsShortcuts && dialog.controller.requiredGame.length === 0
                    columns: width >= desktopShortcut.implicitWidth + menuShortcut.implicitWidth + columnSpacing ? 2 : 1
                    columnSpacing: 16
                    rowSpacing: 0

                    CheckBox {
                        id: desktopShortcut
                        objectName: "installerDesktopShortcut"
                        checked: dialog.installation.desktopShortcutDefault
                        text: qsTr("Create desktop shortcut")
                    }

                    CheckBox {
                        id: menuShortcut
                        objectName: "installerMenuShortcut"
                        checked: dialog.installation.menuShortcutDefault
                        text: qsTr("Create application menu shortcut")
                    }
                }

                Label {
                    Layout.fillWidth: true
                    visible: text.length > 0
                    text: dialog.controller.loading ? qsTr("Preparing installation…") : dialog.controller.error
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                    color: dialog.controller.loading ? Theme.textMuted : Theme.danger
                }

                ProgressBar {
                    Layout.fillWidth: true
                    visible: dialog.controller.loading
                    indeterminate: visible
                    Accessible.name: qsTr("Preparing installation…")
                }
            }
        }

        ScrollView {
            id: existingScroll
            clip: true
            contentWidth: availableWidth
            contentHeight: existingForm.implicitHeight

            ExistingGameForm {
                id: existingForm
                width: existingScroll.availableWidth
                controller: dialog.controller
            }
        }

        InstallationPage {
            installation: dialog.installation
        }
    }

    footer: Control {
        leftPadding: dialog.leftPadding
        rightPadding: dialog.rightPadding
        topPadding: 16
        bottomPadding: 18

        contentItem: RowLayout {
            spacing: Theme.spacing

            Button {
                objectName: "installerBack"
                text: qsTr("Back")
                visible: dialog.usingExisting
                enabled: !dialog.controller.importing
                onClicked: dialog.usingExisting = false
            }

            Button {
                objectName: "useExistingGame"
                text: qsTr("Use Existing Files…")
                icon.source: Theme.icon("folder")
                visible: dialog.controller.canImportExisting && !dialog.usingExisting && !dialog.installation.active
                onClicked: dialog.usingExisting = true
            }

            Item {
                Layout.fillWidth: true
            }

            Button {
                objectName: "installerClose"
                text: dialog.installation.active ? qsTr("Close") : qsTr("Cancel")
                visible: !dialog.installation.running
                enabled: !dialog.controller.importing
                onClicked: dialog.reject()
            }

            Button {
                objectName: "installerStart"
                highlighted: true
                text: dialog.controller.startLabel
                visible: !dialog.usingExisting && dialog.controller.selected && !dialog.installation.active && dialog.controller.requiredGame.length === 0
                enabled: !dialog.controller.loading && (!dialog.controller.createsGameFolder || dialog.controller.directory.trim().length > 0)
                onClicked: dialog.controller.install(desktopShortcut.checked, menuShortcut.checked, bonusContent.checked, installPrefix.text)
            }

            Button {
                objectName: "importExistingGame"
                highlighted: true
                text: qsTr("Link Game")
                visible: dialog.usingExisting
                enabled: existingForm.ready
                onClicked: existingForm.submit()
            }

            Button {
                objectName: "installerCancel"
                text: qsTr("Cancel Installation")
                visible: dialog.installation.running && dialog.installation.stage !== "cancelling"
                onClicked: cancelConfirmation.open()
            }

            Button {
                objectName: "installerLaunch"
                highlighted: true
                text: qsTr("Launch")
                visible: dialog.installation.stage === "finished" && dialog.installation.installedGameId.length > 0
                onClicked: {
                    dialog.installation.launch()
                    dialog.close()
                }
            }
        }
    }
}
