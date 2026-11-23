import QtQuick
import QtQuick.Templates as T

T.CheckBox {
    id: control

    implicitWidth: Math.max(implicitBackgroundWidth + leftInset + rightInset,
                            implicitContentWidth + leftPadding + rightPadding)
    implicitHeight: Math.max(implicitBackgroundHeight + topInset + bottomInset,
                             implicitContentHeight + topPadding + bottomPadding,
                             implicitIndicatorHeight + topPadding + bottomPadding)

    padding: 5
    spacing: 10
    opacity: enabled ? 1 : 0.45

    indicator: CheckIndicator {
        checkState: control.checkState
        down: control.down
        hovered: control.hovered
        visualFocus: control.visualFocus
        x: control.text ? (control.mirrored ? control.width - width - control.rightPadding : control.leftPadding)
                        : control.leftPadding + (control.availableWidth - width) / 2
        y: control.topPadding + (control.availableHeight - height) / 2
    }

    contentItem: Text {
        leftPadding: control.indicator && !control.mirrored ? control.indicator.width + control.spacing : 0
        rightPadding: control.indicator && control.mirrored ? control.indicator.width + control.spacing : 0
        text: control.text.replace(/&(.)/g, "$1")
        textFormat: Text.PlainText
        verticalAlignment: Text.AlignVCenter
        Accessible.ignored: true
        font: control.font
        color: Theme.text
        wrapMode: Text.WordWrap
    }
}
