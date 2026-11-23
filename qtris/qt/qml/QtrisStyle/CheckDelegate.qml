import QtQuick
import QtQuick.Templates as T

T.CheckDelegate {
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

    indicator: CheckIndicator {
        checkState: control.checkState
        down: control.down
        hovered: control.hovered
        visualFocus: control.visualFocus
        x: control.mirrored ? control.leftPadding : control.width - width - control.rightPadding
        y: control.topPadding + (control.availableHeight - height) / 2
    }

    contentItem: ControlLabel {
        button: control
        leftPadding: control.mirrored ? control.indicator.width + control.spacing : 0
        rightPadding: !control.mirrored ? control.indicator.width + control.spacing : 0
        centered: control.display === T.AbstractButton.IconOnly || control.display === T.AbstractButton.TextUnderIcon
        color: Theme.text
    }

    background: Rectangle {
        implicitHeight: 38
        radius: Theme.radius
        color: control.down ? Theme.pressed : control.hovered || control.highlighted ? Theme.hover : "transparent"
    }
}
