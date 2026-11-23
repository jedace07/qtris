"""Type declarations for PySide's QML decorators and named Qt properties."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from PySide6.QtCore import Property as QtProperty
    from PySide6.QtCore import QObject, Signal

    # PySide's stubs omit string Qt types and the call operator for QmlUncreatable
    class Property(QtProperty):
        def __init__(
            self,
            qt_type: type | str,
            fget: Callable[..., object] | None = None,
            /,
            *,
            notify: Signal | None = None,
            constant: bool = False,
        ) -> None: ...

    def QmlUncreatable[T: QObject](reason: str, /) -> Callable[[type[T]], type[T]]: ...
else:
    from PySide6.QtCore import Property as Property
    from PySide6.QtQml import QmlUncreatable as QmlUncreatable
