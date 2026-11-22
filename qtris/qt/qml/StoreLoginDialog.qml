import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs as NativeDialogs
import QtQuick.Layouts
import QtrisStyle

Dialog {
    id: dialog
    objectName: "storeLoginDialog"

    required property QtObject controller
    readonly property string inputKind: controller.login.inputKind ?? ""
    readonly property bool needsCookies: inputKind === "cookies"
    readonly property bool approvesCode: inputKind === "device"

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(490, parent.width - 32)
    title: qsTr("Sign in to %1").arg(controller.login.name ?? "")
    modal: true
    closePolicy: controller.busy ? Popup.NoAutoClose : Popup.CloseOnEscape

    NativeDialogs.FileDialog {
        id: cookiePicker

        title: qsTr("Select the store’s cookies.txt export")
        nameFilters: [qsTr("Text files (*.txt)"), qsTr("All files (*)")]
        onAccepted: dialog.controller.submit_cookie_file(selectedFile)
    }

    contentItem: ColumnLayout {
        spacing: 12

        Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            text: {
                switch (dialog.inputKind) {
                case "device":
                    return qsTr("Sign in to Epic Games in your browser, then approve the request if it shows this code. Qtris uses the Epic Games Launcher’s sign-in, so the page won’t name Qtris.")
                case "api_key":
                    return qsTr("Open the browser, create an API key for Qtris, and paste it here.")
                case "cookies":
                    return qsTr("Sign in in your browser. Export only this store’s cookies in Netscape cookies.txt format, then select the file.")
                case "ubisoft":
                    return qsTr("After signing in, open your browser’s developer tools and find local storage for connect.ubisoft.com. Paste PRODloginData here, or combine PRODloginData, PRODrememberMe and PRODlastProfile in a JSON array. Use null for missing values. Do not share these values.")
                default:
                    return qsTr("Sign in in your browser, then copy the full address from the final page and paste it here.")
                }
            }
        }

        Label {
            objectName: "storeLoginCode"
            Layout.alignment: Qt.AlignHCenter
            visible: dialog.approvesCode
            text: dialog.controller.login.userCode ?? ""
            textFormat: Text.PlainText
            font.family: "monospace"
            font.pointSize: Theme.titleSize
            font.bold: true
            font.letterSpacing: 2
        }

        Button {
            objectName: "openStoreBrowser"
            Layout.alignment: dialog.approvesCode ? Qt.AlignHCenter : Qt.AlignLeft
            visible: !dialog.approvesCode || dialog.controller.waiting
            enabled: !dialog.controller.busy
            text: qsTr("Open Browser")
            onClicked: dialog.controller.open_browser()
        }

        TextField {
            id: response
            objectName: "storeLoginResponse"
            Layout.fillWidth: true
            visible: !dialog.needsCookies && !dialog.approvesCode
            enabled: !dialog.controller.busy
            placeholderText: qsTr("Paste sign-in response")
            Accessible.name: placeholderText
            echoMode: TextInput.Password
            onAccepted: if (finish.enabled) finish.clicked()
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
            running: dialog.controller.busy || dialog.controller.waiting
        }

        Label {
            Layout.alignment: Qt.AlignHCenter
            visible: dialog.approvesCode && (dialog.controller.busy || dialog.controller.waiting)
            text: dialog.controller.waiting ? qsTr("Waiting for your approval…") : qsTr("Signing in…")
            color: Theme.textMuted
        }
    }

    footer: DialogButtonBox {
        Button {
            text: qsTr("Cancel")
            enabled: !dialog.controller.busy
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
        }

        Button {
            objectName: "retryDeviceLogin"
            visible: dialog.approvesCode && !dialog.controller.waiting && !dialog.controller.busy
            highlighted: true
            text: qsTr("Try Again")
            DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
            onClicked: dialog.controller.retry_login()
        }

        Button {
            id: finish
            objectName: "finishStoreLogin"
            visible: !dialog.approvesCode
            highlighted: true

            text: dialog.needsCookies ? qsTr("Select Cookies…") : qsTr("Sign In")
            enabled: !dialog.controller.busy && (dialog.inputKind === "cookies" || response.text.trim().length > 0)
            DialogButtonBox.buttonRole: DialogButtonBox.ActionRole
            onClicked: {
                if (dialog.needsCookies)
                    cookiePicker.open()
                else {
                    dialog.controller.submit(response.text)
                    response.clear()
                }
            }
        }
    }
}
