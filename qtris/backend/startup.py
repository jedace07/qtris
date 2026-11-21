"""Starting the backend.

While the backend runs in the frontend's process, the frontend starts it; this is
not an API call, and will be what the backend's own process does once there is one.
"""

from qtris import settings
from qtris.startup import init_qtris, run_all_checks
from qtris.util.log import logger


def start_backend(run_checks_async: bool) -> None:
    """Open the database and run the startup checks; on a worker thread if
    run_checks_async is set."""
    logger.info("Starting Qtris %s", settings.VERSION)
    init_qtris()
    run_all_checks(async_ops=run_checks_async)
