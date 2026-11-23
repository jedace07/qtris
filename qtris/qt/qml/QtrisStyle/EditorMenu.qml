import QtQuick
import QtQuick.Templates as T

Menu {
    id: editMenu

    // TextInput and TextEdit expose the same editing actions
    required property var editor

    popupType: Qt.platform.pluginName === "wayland" ? T.Popup.Item : T.Popup.Window

    MenuItem {
        text: qsTranslate("UndoAction", "Undo")
        icon.name: "edit-undo"
        enabled: editMenu.editor.canUndo
        onTriggered: editMenu.editor.undo()
    }

    MenuItem {
        text: qsTranslate("RedoAction", "Redo")
        icon.name: "edit-redo"
        enabled: editMenu.editor.canRedo
        onTriggered: editMenu.editor.redo()
    }

    MenuSeparator {}

    MenuItem {
        text: qsTranslate("CutAction", "Cut")
        icon.name: "edit-cut"
        enabled: !editMenu.editor.readOnly && editMenu.editor.selectionStart !== editMenu.editor.selectionEnd
        onTriggered: editMenu.editor.cut()
    }

    MenuItem {
        text: qsTranslate("CopyAction", "Copy")
        icon.name: "edit-copy"
        enabled: editMenu.editor.selectionStart !== editMenu.editor.selectionEnd
        onTriggered: editMenu.editor.copy()
    }

    MenuItem {
        text: qsTranslate("PasteAction", "Paste")
        icon.name: "edit-paste"
        enabled: editMenu.editor.canPaste
        onTriggered: editMenu.editor.paste()
    }

    MenuItem {
        text: qsTranslate("DeleteAction", "Delete")
        icon.name: "edit-delete"
        enabled: !editMenu.editor.readOnly && editMenu.editor.selectionStart !== editMenu.editor.selectionEnd
        onTriggered: editMenu.editor.remove(editMenu.editor.selectionStart, editMenu.editor.selectionEnd)
    }

    MenuSeparator {}

    MenuItem {
        text: qsTranslate("SelectAllAction", "Select All")
        icon.name: "edit-select-all"
        onTriggered: editMenu.editor.selectAll()
    }
}
