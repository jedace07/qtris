import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs as NativeDialogs
import QtQuick.Layouts

Dialog {
    id: dialog
    objectName: "storeLoginDialog"

    required property QtObject controller
    readonly property string inputKind: controller.login.inputKind ?? ""
    readonly property bool needsCookies: inputKind === "cookies"
    readonly property bool waitsForBrowser: inputKind === "itch_oauth"

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(490, parent.width - 32)
    title: qsTr("Sign in to %1").arg(controller.login.name ?? "")
    modal: true
    closePolicy: controller.busy ? Popup.NoAutoClose : Popup.CloseOnEscape

    NativeDialogs.FileDialog {
        id: cookiePicker

        title: qsTr("Select the store’s cookie export")
        nameFilters: [qsTr("Cookie exports (*.json *.txt)"), qsTr("All files (*)")]
        onAccepted: dialog.controller.submit_cookie_file(selectedFile)
    }

    contentItem: ColumnLayout {
        spacing: 12

        Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: {
                switch (dialog.inputKind) {
                case "epic_code":
                    return qsTr("Sign in to Epic Games in your browser, then copy the full response from the final page and return to Qtris. You can also paste the authorization code below.");
                case "itch_oauth":
                    return qsTr("Approve access to your itch.io library in your browser. Qtris will complete sign-in automatically.");
                case "api_key":
                    return qsTr("Open the browser, create an API key for Qtris, and paste it here.");
                case "cookies":
                    return qsTr("Sign in through your browser, then export this store’s cookies as JSON and select the file. Netscape cookies.txt exports also work.");
                case "ubisoft":
                    return qsTr("After signing in, open your browser’s developer tools and find local storage for connect.ubisoft.com. Paste PRODloginData here, or combine PRODloginData, PRODrememberMe and PRODlastProfile in a JSON array. Use null for missing values. Do not share these values.");
                default:
                    return "";
                }
            }
        }

        Button {
            objectName: "openStoreBrowser"
            Layout.alignment: Qt.AlignLeft
            enabled: !dialog.controller.busy
            text: qsTr("Open Browser")
            onClicked: dialog.controller.open_browser()
        }

        TextField {
            id: response
            objectName: "storeLoginResponse"
            Layout.fillWidth: true
            visible: !dialog.needsCookies && !dialog.waitsForBrowser
            enabled: !dialog.controller.busy
            placeholderText: qsTr("Paste sign-in response")
            Accessible.name: placeholderText
            echoMode: TextInput.Password
            onAccepted: if (finish.enabled)
                finish.clicked()
        }

        Label {
            Layout.fillWidth: true
            visible: text.length > 0
            text: dialog.controller.error
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
        }

        BusyIndicator {
            Layout.alignment: Qt.AlignHCenter
            visible: running
            running: dialog.controller.busy
        }
    }

    footer: DialogButtonBox {
        Button {
            text: qsTr("Cancel")
            enabled: !dialog.controller.busy
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
        }

        Button {
            objectName: "useItchIoApiKey"
            visible: dialog.waitsForBrowser
            text: qsTr("Use API Key for Collections…")
            enabled: !dialog.controller.busy
            DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
            onClicked: dialog.controller.use_itchio_api_key()
        }

        Button {
            id: finish
            objectName: "finishStoreLogin"
            highlighted: true
            visible: !dialog.waitsForBrowser

            text: dialog.needsCookies ? qsTr("Select Cookies…") : qsTr("Sign In")
            enabled: !dialog.controller.busy && (dialog.inputKind === "cookies" || response.text.trim().length > 0)
            DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
            onClicked: {
                if (dialog.needsCookies)
                    cookiePicker.open();
                else {
                    dialog.controller.submit(response.text);
                    response.clear();
                }
            }
        }
    }
}
