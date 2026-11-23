"""Render themed and tinted icons with Qt's public image APIs."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QUrl, QUrlQuery
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtQuick import QQuickImageProvider


class IconProvider(QQuickImageProvider):
    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Pixmap)

    def requestPixmap(self, id: str, size: QSize, requestedSize: QSize, /) -> QPixmap:  # noqa: A002, N802
        options = QUrlQuery(QUrl(f"image://qtris-icons/{id}"))
        decode = QUrl.ComponentFormattingOption.FullyDecoded
        source = QUrl(options.queryItemValue("source", decode))
        fallback = source.toLocalFile()
        if source.scheme() == "qrc":
            fallback = f":{source.path()}"
        name = options.queryItemValue("name", decode)
        icon = QIcon.fromTheme(name, QIcon(fallback)) if name else QIcon(fallback)
        extent = requestedSize if requestedSize.isValid() else QSize(24, 24)
        pixmap = icon.pixmap(extent)
        if icon.isNull() and not fallback:
            pixmap = QPixmap(extent)
            pixmap.fill(Qt.GlobalColor.transparent)
        tint = QColor(options.queryItemValue("color", decode))
        if not pixmap.isNull() and tint.isValid() and tint.alpha() > 0:
            if not pixmap.hasAlphaChannel():
                image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
                pixmap = QPixmap.fromImage(image)
            with QPainter(pixmap) as painter:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                painter.fillRect(pixmap.rect(), tint)
        size.setWidth(pixmap.width())
        size.setHeight(pixmap.height())
        return pixmap
