pragma Singleton

import QtQuick

// The colors, metrics and type sizes that the Qtris style and views share
QtObject {
    id: theme

    // The theme preference: "light", "dark", or "default" to follow the desktop
    property string preference: "default"
    // Dark unless the preference or the desktop asks for light; not every platform
    // takes the preference on, so it's followed here too
    readonly property bool dark: preference === "dark"
                                 || preference !== "light" && Application.styleHints.colorScheme !== Qt.ColorScheme.Light

    // Surfaces, from the window back to the popups in front of it
    readonly property color window: dark ? "#141319" : "#f6f5f9"
    readonly property color sidebar: dark ? "#1b1a21" : "#ecebf2"
    readonly property color surface: dark ? "#201f27" : "#ffffff"
    readonly property color raised: dark ? "#26252e" : "#ffffff"
    readonly property color field: dark ? "#17161c" : "#ffffff"
    readonly property color control: dark ? "#2b2a33" : "#ffffff"
    readonly property color controlHover: dark ? "#34333e" : "#f2f1f6"
    readonly property color controlPressed: dark ? "#3d3c48" : "#e7e5ee"
    readonly property color hover: dark ? Qt.rgba(1, 1, 1, 0.06) : Qt.rgba(0, 0, 0, 0.05)
    readonly property color pressed: dark ? Qt.rgba(1, 1, 1, 0.1) : Qt.rgba(0, 0, 0, 0.09)
    // The groove of switches and progress bars
    readonly property color track: dark ? "#3b3a47" : "#d5d3de"
    readonly property color border: dark ? "#2e2d37" : "#dddbe5"
    readonly property color borderStrong: dark ? "#3c3b47" : "#c8c6d2"
    readonly property color scrim: Qt.rgba(0, 0, 0, dark ? 0.6 : 0.35)

    // Text and icons
    readonly property color text: dark ? "#edecf2" : "#1d1c23"
    readonly property color textMuted: dark ? "#a4a2b0" : "#5f5d6b"
    readonly property color textSubtle: dark ? "#706e7d" : "#8e8c9a"

    // The Qtris violet, from the logo, and the states that grow from it
    readonly property color accent: dark ? "#a58bff" : "#6a3de8"
    readonly property color accentHover: dark ? "#b5a0ff" : "#774cf0"
    readonly property color accentPressed: dark ? "#9175f5" : "#5a2fd0"
    readonly property color accentSubtle: dark ? Qt.rgba(0.65, 0.55, 1, 0.16) : Qt.rgba(0.42, 0.24, 0.91, 0.12)
    readonly property color accentText: dark ? "#bfadff" : "#5a2fd0"
    readonly property color textOnAccent: dark ? "#160c33" : "#ffffff"

    readonly property color danger: dark ? "#f2615d" : "#d33a3a"
    readonly property color dangerHover: dark ? "#f57874" : "#dd4c4c"
    readonly property color warning: dark ? "#f0b546" : "#a8690c"
    readonly property color success: dark ? "#4fc88d" : "#1d9a61"
    readonly property color textOnDanger: "#ffffff"

    // Metrics
    readonly property int radiusSmall: 6
    readonly property int radius: 8
    readonly property int radiusLarge: 12
    readonly property int controlHeight: 34
    readonly property int spacing: 8
    readonly property int animation: 120

    // Type sizes, relative to the desktop's font
    readonly property real fontSize: Application.font.pointSize > 0 ? Application.font.pointSize : 10
    readonly property real smallSize: fontSize * 0.88
    readonly property real largeSize: fontSize * 1.15
    readonly property real titleSize: fontSize * 1.35
    readonly property real headingSize: fontSize * 1.8

    // An icon of the style's icon set
    function icon(name: string): url {
        return name.length > 0 ? Qt.resolvedUrl("icons/" + name + ".svg") : ""
    }
}
