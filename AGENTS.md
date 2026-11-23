# Instructions

- Use numpy docstring style when asked to write docstrings.
- Comments should start with a capital letter but not end with a period.
- Strictly typed, target Python 3.14.
- Follow Pyright and Ruff rules. Run `uv run python utils/check-pyright.py` to check for new type errors using the same baseline as CI.
- Use `uv` for package management.
- Use `uv add` to add packages.
- Use `uv run` to run files.
- Use logging instead of prints with a `log` variable, unless the project already has a preference.
- Use `from __future__ import annotations` and `if TYPE_CHECKING:` for typing.
- Prefer asyncio and multiprocessing where possible.
- Make clean seperations of module files per functionality, making use of package folders with `__init__.py`.

## Qt for Python and QML

- Keep application logic in Python and presentation logic in QML. Use focused `QObject` bridge or model classes for the interface between them.
- Access Qt UI objects and QML-facing models on the GUI thread. Hand worker results back through a queued Qt signal or the application event loop. Use asynchronous replies for new dialogs.
- Use `QtQuick.Controls` in views and `QtQuick.Templates` for control implementations in `QtrisStyle`. Keep Basic as the style fallback and use public Qt APIs.
- Prefer registered QML types when editor completion and static QML analysis matter. Use `@QmlElement` to expose a type and `@QmlSingleton` only when singleton lifetime and global access are appropriate.
- Register Python-owned controllers and models in `QtrisBridge` as uncreatable types. Pass the instances through typed required properties. Use explicit Qt names such as `QVariantList` for container properties so the metadata generator records their QML types.
- Import modules containing decorated QML types before loading the QML engine so registration occurs at runtime.
- Use typed `@Slot` methods for operations called from QML. Declare every argument type and use `result=` for return values.
- Use `PySide6.QtCore.Property`, not Python's built-in `property`, for values exposed to QML. Mutable properties should have a notification `Signal` passed through `notify=` and should emit it only when the value actually changes.
- Keep Python annotations consistent with the Qt types declared by `Property`, `Signal`, and `Slot`. Python annotations are used by the Python type checker; Qt declarations define the API visible to QML.
- The `@Property` getter/setter form may require a narrow `# pyright: ignore[reportRedeclaration]` because the setter repeats the property name. Do not disable the diagnostic globally.
- QML modules declared from Python need `QML_IMPORT_NAME`, `QML_IMPORT_MAJOR_VERSION`, and optionally `QML_IMPORT_MINOR_VERSION`.
- Keep the Python entry point, Python files declaring registered QML types, and at least one QML source in `[tool.pyside6-project].files` so `pyside6-project build` can generate QML module metadata.
- Run `uv run pyside6-project build` to regenerate `.qmltypes` and `qmldir` metadata. Rebuild after changing QML decorators, exposed classes, properties, signals, slots, Qt signatures, module names or versions, or the PySide6 environment. Implementation-only method changes and QML layout changes do not normally require regeneration.
- Validate all QML with `uv run python utils/check-qml.py`. This runs `pyside6-project build` and generates combined module metadata in `build/qml` for Qt tooling. Add `build/qml` and `qtris/qt/qml` to the QML language server's import paths. If generated information is stale, run `uv run pyside6-project clean`, rerun the check, and restart the QML language server.
- Treat `.qmltypes`, metatype JSON, generated registration sources, and generated `qmldir` files as build artifacts. Do not edit them manually.
- Use the QML language server and command-line tools from the same Qt/PySide6 environment as the application to avoid version and import-path mismatches.
- Format QML with `uv run pyside6-qmlformat --inplace <files>` and keep QML linting alongside Python type and lint checks.
