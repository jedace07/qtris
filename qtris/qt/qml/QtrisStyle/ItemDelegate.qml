import QtQuick
import QtQuick.Templates as T

T.ItemDelegate {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding,
                             implicitIndicatorHeight + topPadding + bottomPadding)

    padding: 8
    horizontalPadding: 12
    spacing: 10
    opacity: enabled ? 1 : 0.45

    icon.width: 18
    icon.height: 18
    icon.color: highlighted ? Theme.accentText : Theme.textMuted

    contentItem: ControlLabel {
        button: control
        centered: control.display === T.AbstractButton.IconOnly || control.display === T.AbstractButton.TextUnderIcon
        color: control.highlighted ? Theme.accentText : Theme.text
    }

    background: Rectangle {
        implicitHeight: 36
        radius: Theme.radius
        color: control.highlighted ? Theme.accentSubtle : control.down ? Theme.pressed
             : control.hovered ? Theme.hover : "transparent"
        border.width: control.visualFocus ? 2 : 0
        border.color: Theme.accent
    }
}
