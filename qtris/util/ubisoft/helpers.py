NOT_INSTALLED = "NOT_INSTALLED"
INSTALLED = "INSTALLED"


def get_ubisoft_registry(prefix, fullpath):
    """Get a value from the registry in a Ubisoft Connect prefix"""
    if not fullpath:
        return ""
    if fullpath.startswith("HKEY"):
        path, key = fullpath.rsplit("\\", maxsplit=1)
        path = convert_ubisoft_key(path)
        return prefix.get_registry_key(path, key)
    return fullpath


def convert_ubisoft_key(key_path):
    """Convert Ubisoft registry keys for Wine compatibility"""
    if "LOCAL_MACHINE" in key_path:
        key_path = key_path.replace("SOFTWARE\\", "Software\\Wow6432Node\\")
    key_path = key_path.replace("\\UBISOFT", "\\Ubisoft")
    key_path = key_path.replace("\\", "/")
    return key_path
