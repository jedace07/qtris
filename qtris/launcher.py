"""Bootstrap the Qtris console and Qt Quick application."""

import locale
import multiprocessing
import sys

from qtris.util.textdomain import bind_text_domain


def main() -> int:
    multiprocessing.set_start_method("fork")

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error as error:
        sys.stderr.write(f"Unsupported locale setting: {error}\n")

    bind_text_domain()

    from qtris.cli.application import CommandLineApplication
    from qtris.util.mainloop import run_asyncio_on_glib

    run_asyncio_on_glib()
    command_line_app = CommandLineApplication()
    status = command_line_app.run(sys.argv)
    if not command_line_app.needs_frontend:
        return status

    try:
        from qtris.qt.application import QtrisApplication
    except ImportError as error:
        sys.stderr.write(f"Error importing the Qtris Qt frontend: {error}\n")
        return 1

    return QtrisApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
