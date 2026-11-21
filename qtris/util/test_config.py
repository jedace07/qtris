import os

from qtris.database import schema
from qtris.startup import init_qtris


def setup_test_environment():
    """Sets up a system to be able to run tests"""
    os.environ["QTRIS_SKIP_INIT"] = "1"
    schema.syncdb()
    init_qtris()
