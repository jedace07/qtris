import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtWebEngine
import QtrisStyle

// Signs in to a store in Qtris's own browser view. The controller watches where the
// view goes and finishes the sign-in once the store hands over its response
ApplicationWindow {
    id: signIn
    objectName: "storeWebLoginWindow"

    required property QtObject controller
    readonly property string pageHost: {
        try {
            return new URL(view.url).host
        } catch (error) {
            return ""
        }
    }
    readonly property bool securePage: String(view.url).startsWith("https:")

    signal dismissed

    title: qsTr("Sign in to %1").arg(controller.login.name ?? "")
    width: 540
    height: Math.min(780, Screen.desktopAvailableHeight - 80)
    minimumWidth: 400
    minimumHeight: 480
    modality: Qt.WindowModal
    visible: true

    // The window stays open while the sign-in finishes
    onClosing: close => close.accepted = !signIn.controller.busy
    onVisibleChanged: {
        if (!visible)
            dismissed()
    }

    // A private profile per sign-in, so no store session outlives it
    WebEngineProfilePrototype {
        id: profilePrototype
    }

    header: ToolBar {
        RowLayout {
            anchors.fill: parent
            spacing: 4

            ToolButton {
                enabled: view.canGoBack && !signIn.controller.busy
                icon.source: Theme.icon("chevron-left")
                Accessible.name: qsTr("Back")
                ToolTip.visible: hovered
                ToolTip.text: qsTr("Back")
                onClicked: view.goBack()
            }

            ToolButton {
                enabled: !signIn.controller.busy
                icon.source: Theme.icon("refresh")
                Accessible.name: qsTr("Reload")
                ToolTip.visible: hovered
                ToolTip.text: qsTr("Reload")
                onClicked: view.reload()
            }

            Icon {
                visible: !signIn.securePage && signIn.pageHost.length > 0
                symbol: "warning"
                color: Theme.warning
            }

            Label {
                Layout.fillWidth: true
                text: signIn.pageHost
                color: Theme.textMuted
                elide: Text.ElideMiddle
                Accessible.name: qsTr("Page address")
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Pane {
            Layout.fillWidth: true
            visible: signIn.controller.error.length > 0

            RowLayout {
                anchors.fill: parent
                spacing: Theme.spacing

                Icon {
                    symbol: "warning"
                    color: Theme.danger
                }

                Label {
                    Layout.fillWidth: true
                    text: signIn.controller.error
                    textFormat: Text.PlainText
                    wrapMode: Text.WordWrap
                }

                Button {
                    objectName: "retryStoreLogin"
                    text: qsTr("Try Again")
                    onClicked: {
                        signIn.controller.clear_error()
                        const start = signIn.controller.login.url ?? ""
                        if (String(view.url) === start)
                            view.reload()
                        else
                            view.url = start
                    }
                }
            }
        }

        WebEngineView {
            id: view
            objectName: "storeWebLoginView"

            Layout.fillWidth: true
            Layout.fillHeight: true
            onUrlChanged: signIn.controller.web_url_changed(url)
            // Sign-in pages that open a window, such as a third-party account's, stay in this one
            onNewWindowRequested: request => request.openIn(view)

            Component.onCompleted: {
                // The prototype creates the private profile during component completion
                view.profile = profilePrototype.instance()
                // The controller sets the profile up before the first page loads
                signIn.controller.attach_web_profile(view.profile)
                view.url = signIn.controller.login.url ?? ""
            }

            Rectangle {
                anchors.fill: parent
                visible: signIn.controller.busy
                color: Theme.scrim

                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: Theme.spacing

                    BusyIndicator {
                        Layout.alignment: Qt.AlignHCenter
                        running: parent.visible
                    }

                    Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: qsTr("Signing in…")
                        color: "white"
                    }
                }

                // Keeps clicks off the page while the sign-in finishes
                MouseArea {
                    anchors.fill: parent
                    acceptedButtons: Qt.AllButtons
                }
            }
        }
    }
}
