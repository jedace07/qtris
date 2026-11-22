import QtQuick

MouseArea {
    id: wheelArea

    required property Flickable view
    readonly property alias scrolling: animation.running
    readonly property real minimumY: view.originY - view.topMargin
    readonly property real maximumY: Math.max(minimumY, view.originY + view.contentHeight - view.height + view.bottomMargin)
    readonly property real scrollY: Math.max(minimumY, Math.min(maximumY, animatedY))
    property real animatedY: 0

    parent: view
    anchors.fill: parent
    acceptedButtons: Qt.NoButton
    enabled: view.interactive
    Accessible.ignored: true

    onWheel: event => {
        // Let Qt handle trackpad gestures and horizontal scrolling
        if (event.phase !== Qt.NoScrollPhase || event.pixelDelta.x || event.pixelDelta.y || !event.angleDelta.y) {
            if (event.phase === Qt.ScrollBegin || event.pixelDelta.y)
                animation.stop();
            event.accepted = false;
            return;
        }

        const current = wheelArea.view.contentY;
        const delta = -event.angleDelta.y * Application.styleHints.wheelScrollLines * 36 / 120;
        // Add each tick to the pending destination, and reverse from the current position
        const pending = animation.to - current;
        const start = animation.running && pending * delta > 0 ? animation.to : current;
        const destination = Math.max(wheelArea.minimumY, Math.min(wheelArea.maximumY, start + delta));
        if (destination === current) {
            event.accepted = false;
            return;
        }

        animation.stop();
        animation.from = current;
        animation.to = destination;
        animation.start();
    }

    onScrollYChanged: {
        if (scrolling)
            view.contentY = scrollY;
    }

    Connections {
        target: wheelArea.view

        function onContentYChanged() {
            // Yield to keyboard navigation, scrollbar drags, and other position changes
            if (wheelArea.scrolling && wheelArea.view.contentY !== wheelArea.scrollY)
                animation.stop();
        }

        function onDragStarted() {
            animation.stop();
        }
    }

    NumberAnimation {
        id: animation
        target: wheelArea
        property: "animatedY"
        duration: 180
        easing.type: Easing.OutCubic
    }
}
