import QtQuick

// One icon of the style's icon set, tinted to match the text around it
IconGraphic {
    property string symbol: ""
    property int size: 18

    fallbackSource: Theme.icon(symbol)
    sourceSize: Qt.size(size, size)
    tint: Theme.text
}
