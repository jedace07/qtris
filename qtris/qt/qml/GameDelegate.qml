import QtQuick.Controls

ItemDelegate {
    required property int index
    required property string gameTitle
    required property string runnerText
    required property string platformText
    required property string playTimeText
    required property string lastPlayedText
    required property bool installed
    required property bool running
    required property url coverUrl
    required property url bannerUrl
    required property url iconUrl
    required property string iconName
}
