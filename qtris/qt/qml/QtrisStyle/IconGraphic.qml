import QtQuick

Image {
    id: graphic

    property string name: ""
    property url fallbackSource: ""
    property color tint: "transparent"

    fillMode: Image.PreserveAspectFit
    source: name.length === 0 && tint.a === 0 ? fallbackSource : name.length === 0 && fallbackSource.toString().length === 0 ? "" : "image://qtris-icons/icon?name=" + encodeURIComponent(name) + "&source=" + encodeURIComponent(fallbackSource.toString()) + "&color=" + encodeURIComponent(tint.toString())
    Accessible.ignored: true
}
