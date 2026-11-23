import QtQuick
import QtQuick.Templates as T

Item {
    id: label

    required property T.AbstractButton button
    property alias font: caption.font
    property color color: Theme.text
    property real leftPadding: 0
    property real rightPadding: 0
    property bool centered: true

    readonly property bool vertical: button.display === T.AbstractButton.TextUnderIcon
    readonly property bool hasIcon: button.display !== T.AbstractButton.TextOnly && (button.icon.name.length > 0 || button.icon.source.toString().length > 0)
    readonly property bool hasText: button.display !== T.AbstractButton.IconOnly && button.text.length > 0
    readonly property real gap: hasIcon && hasText ? button.spacing : 0
    readonly property real availableWidth: Math.max(0, width - leftPadding - rightPadding)
    readonly property real iconWidth: hasIcon ? (button.icon.width || 18) : 0
    readonly property real iconHeight: hasIcon ? (button.icon.height || 18) : 0

    implicitWidth: leftPadding + rightPadding + (vertical ? Math.max(iconWidth, caption.implicitWidth) : iconWidth + gap + caption.implicitWidth)
    implicitHeight: vertical ? iconHeight + gap + caption.implicitHeight : Math.max(iconHeight, caption.implicitHeight)
    font: button.font
    Accessible.ignored: true

    Grid {
        columns: label.vertical ? 1 : 2
        layoutDirection: label.button.mirrored ? Qt.RightToLeft : Qt.LeftToRight
        horizontalItemAlignment: label.vertical ? Grid.AlignHCenter : Grid.AlignLeft
        verticalItemAlignment: Grid.AlignVCenter
        spacing: label.gap
        x: label.leftPadding + (label.centered ? (label.availableWidth - width) / 2 : label.button.mirrored ? label.availableWidth - width : 0)
        y: (label.height - height) / 2

        IconGraphic {
            width: Math.min(label.availableWidth, label.iconWidth)
            height: label.iconHeight
            visible: label.hasIcon
            name: label.hasIcon ? label.button.icon.name : ""
            fallbackSource: label.hasIcon ? label.button.icon.source : ""
            tint: label.button.icon.color
            sourceSize: Qt.size(label.iconWidth, label.iconHeight)
        }

        Text {
            id: caption
            visible: label.hasText
            width: Math.min(implicitWidth, Math.max(0, label.availableWidth - (label.vertical ? 0 : label.iconWidth + label.gap)))
            text: label.hasText ? label.button.text.replace(/&(.)/g, "$1") : ""
            textFormat: Text.PlainText
            color: label.color
            elide: Text.ElideRight
        }
    }
}
