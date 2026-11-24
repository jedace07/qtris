from qtris.util.log import logger

try:
    import evdev
except ImportError:
    evdev = None
except AttributeError as err:
    # 'evdev' versions 1.5 and earlier are incompatible with Python 3.11
    # and produce this exception; we won't be able to use these.
    logger.exception("python3-evdev failed to load, and won't be available: %s", err)
    evdev = None


def get_devices():
    if not evdev:
        logger.warning("python3-evdev not installed, controller support not available")
        return []
    _devices = []
    for dev in evdev.list_devices():
        try:
            _devices.append(evdev.InputDevice(dev))
        except RuntimeError:
            pass
    return _devices


def get_joypads():
    """Return a list of tuples with the device and the joypad name"""
    return [(dev.path, dev.name) for dev in get_devices()]


def read_button(device):
    """Reference function for reading controller buttons and axis values.
    Not to be used as is.
    """
    # pylint: disable=no-member
    for event in device.read_loop():
        if event.type == evdev.ecodes.EV_KEY and event.value == 0:
            print("button %s (%s): %s" % (event.code, hex(event.code), event.value))
        if event.type == evdev.ecodes.EV_ABS:
            sticks = (0, 1, 3, 4)
            if event.code not in sticks or abs(event.value) > 5000:
                print("axis %s (%s): %s" % (event.code, hex(event.code), event.value))
