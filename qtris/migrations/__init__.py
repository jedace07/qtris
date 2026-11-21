import importlib

from qtris import settings
from qtris.util.log import logger

MIGRATION_VERSION = 22  # Never decrease this number

# Replace deprecated migrations with empty lists
MIGRATIONS = [
    [],
    [],
    [],
    [],
    [],
    [],
    [],
    [],
    ["migrate_hidden_ids"],
    ["migrate_steam_appids"],
    [],
    [],
    [],
    ["migrate_hidden_category"],
    ["migrate_ge_proton"],
    ["migrate_banners_back"],
    ["migrate_proton_to_wine_dir"],
    [],
    [],
    ["remove_lutris_account"],
    ["retire_standalone_emulators"],
    ["migrate_proton_release_tags"],
]


def get_migration_module(migration_name):
    return importlib.import_module("qtris.migrations.%s" % migration_name)


def migrate():
    current_version = int(settings.read_setting("migration_version") or 0)
    if current_version >= MIGRATION_VERSION:
        return
    for i in range(current_version, MIGRATION_VERSION):
        for migration_name in MIGRATIONS[i]:
            logger.info("Running migration: %s", migration_name)
            migration = get_migration_module(migration_name)
            migration.migrate()

    settings.write_setting("migration_version", MIGRATION_VERSION)
