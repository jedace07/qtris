import logging as log
import math

import yaml

from qtris.util.ubisoft.consts import UBISOFT_CONFIGURATIONS_BLACKLISTED_NAMES


class UbisoftParser(object):
    def __init__(self):
        self.configuration_raw = None
        self.ownership_raw = None
        self.settings_raw = None
        self.parsed_games = None

    def _convert_data(self, data):
        # calculate object size (konrad's formula)
        if data > 256 * 256:
            data = data - (128 * 256 * math.ceil(data / (256 * 256)))
            data = data - (128 * math.ceil(data / 256))
        else:
            if data > 256:
                data = data - (128 * math.ceil(data / 256))
        return data

    def _parse_configuration_header(self, header, second_eight=False):
        try:
            offset = 1
            multiplier = 1
            record_size = 0
            tmp_size = 0

            if second_eight:
                while header[offset] != 0x08 or (header[offset] == 0x08 and header[offset + 1] == 0x08):
                    record_size += header[offset] * multiplier
                    multiplier *= 256
                    offset += 1
                    tmp_size += 1
            else:
                while header[offset] != 0x08 or record_size == 0:
                    record_size += header[offset] * multiplier
                    multiplier *= 256
                    offset += 1
                    tmp_size += 1

            record_size = self._convert_data(record_size)

            offset += 1  # skip 0x08

            # look for launch_id
            multiplier = 1
            launch_id = 0

            while header[offset] != 0x10 or header[offset + 1] == 0x10:
                launch_id += header[offset] * multiplier
                multiplier *= 256
                offset += 1

            launch_id = self._convert_data(launch_id)

            offset += 1  # skip 0x10

            multiplier = 1
            launch_id_2 = 0
            while header[offset] != 0x1A or (header[offset] == 0x1A and header[offset + 1] == 0x1A):
                launch_id_2 += header[offset] * multiplier
                multiplier *= 256
                offset += 1

            launch_id_2 = self._convert_data(launch_id_2)

            # if object size is smaller than 128b, there might be a chance that secondary size will not occupy 2b
            if record_size - offset < 128 <= record_size:
                tmp_size -= 1
                record_size += 1

            # we end up in the middle of header, return values normalized
            # to end of record as well real yaml size and game launch_id
            return record_size - offset, launch_id, launch_id_2, offset + tmp_size + 1
        except Exception:
            # something went horribly wrong, do not crash it,
            # just return 0s, this way it will be handled later in the code
            # 10 is to step a little in configuration file in order to find next game
            return 0, 0, 0, 10

    def _parse_configuration(self):
        configuration_content = self.configuration_raw
        global_offset = 0
        records = {}
        if configuration_content:
            try:
                while global_offset < len(configuration_content):
                    data = configuration_content[global_offset:]
                    object_size, install_id, launch_id, header_size = self._parse_configuration_header(data)

                    launch_id = install_id if launch_id == 0 or launch_id == install_id else launch_id

                    if object_size > 500:
                        records[install_id] = {
                            "size": object_size,
                            "offset": global_offset + header_size,
                            "install_id": install_id,
                            "launch_id": launch_id,
                        }
                    global_offset_tmp = global_offset
                    global_offset += object_size + header_size

                    if global_offset < len(configuration_content) and configuration_content[global_offset] != 0x0A:
                        object_size, _, _, header_size = self._parse_configuration_header(data, True)
                        global_offset = global_offset_tmp + object_size + header_size
            except Exception:
                log.exception("parse_configuration failed with exception. Possibly 'configuration' file corrupted")
        return records

    def _get_field_from_yaml(self, game_yaml, field="name"):
        field_value = ""

        if field in game_yaml["root"]:
            field_value = game_yaml["root"][field] or ""
        # Fallback 1
        if field == "name" and field_value.lower() in UBISOFT_CONFIGURATIONS_BLACKLISTED_NAMES:
            if "installer" in game_yaml["root"] and "game_identifier" in game_yaml["root"]["installer"]:
                field_value = game_yaml["root"]["installer"]["game_identifier"]
        # Fallback 2
        if field_value.lower() in UBISOFT_CONFIGURATIONS_BLACKLISTED_NAMES:
            if (
                "localizations" in game_yaml
                and "default" in game_yaml["localizations"]
                and field_value in game_yaml["localizations"]["default"]
            ):
                field_value = game_yaml["localizations"]["default"][field_value]
        return field_value

    def _get_registry_properties_from_yaml(self, game_yaml):
        try:
            registry_path = game_yaml["root"]["start_game"]["online"]["executables"][0]["working_directory"]["register"]
            try:
                exe = game_yaml["root"]["start_game"]["online"]["executables"][0]["path"]["relative"]
            except KeyError:
                exe = game_yaml["root"]["start_game"]["online"]["executables"][0]["path"]["register"]
            return registry_path, exe
        except KeyError, IndexError:
            return None, None

    def _parse_game(self, game_yaml, install_id, launch_id):
        space_id = ""
        launch_id = str(launch_id)
        install_id = str(install_id)
        if "space_id" in game_yaml["root"]:
            space_id = game_yaml["root"]["space_id"]
        game_name = self._get_field_from_yaml(game_yaml, "name")
        thumb_image = self._get_field_from_yaml(game_yaml, "thumb_image")
        registry_path, exe = self._get_registry_properties_from_yaml(game_yaml)
        return {
            "spaceId": space_id,
            "launchId": launch_id,
            "installId": install_id,
            "name": game_name,
            "thumbImage": thumb_image,
            "registryPath": registry_path,
            "exe": exe,
        }

    def _iter_configuration_items(self):
        configuration_records = self._parse_configuration()
        for _, game in configuration_records.items():
            if game["size"]:
                yield game

    def _parse_configuration_item(self, conf_item):
        stream = self.configuration_raw[conf_item["offset"] : conf_item["offset"] + conf_item["size"]].decode(
            "utf8", errors="ignore"
        )
        if stream and "start_game" in stream:
            return yaml.load(stream.replace("\t", " "), Loader=yaml.FullLoader)

    def parse_games(self, configuration_data):
        self.configuration_raw = configuration_data
        for game in self._iter_configuration_items():
            yaml_object = self._parse_configuration_item(game)
            if yaml_object:
                yield self._parse_game(yaml_object, game["install_id"], game["launch_id"])
