import QtQuick
import QtQuick.Templates as T

T.TabButton {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding)

    padding: 6
    horizontalPadding: 14
    spacing: 6

    icon.width: 16
    icon.height: 16
    icon.color: control.checked ? Theme.text : Theme.textMuted

    contentItem: ControlLabel {
        button: control
        font.pointSize: control.font.pointSize
        font.weight: control.checked ? Font.DemiBold : Font.Normal
        color: control.checked ? Theme.text : control.hovered ? Theme.text : Theme.textMuted
    }

    background: Item {
        implicitHeight: 28
    }
}
