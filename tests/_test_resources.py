from unittest import TestCase

from qtris.api import parse_installer_url


class TestInstallerUrls(TestCase):
    def test_legacy_url(self):
        result = parse_installer_url("qtris:quake")
        self.assertEqual(result["game_slug"], "quake")
        self.assertEqual(result["revision"], None)
        self.assertEqual(result["action"], None)
        self.assertEqual(result["launch_config_name"], None)

    def test_action_rungameid(self):
        result = parse_installer_url("qtris:rungameid/123")
        self.assertEqual(result["game_slug"], "123")
        self.assertEqual(result["revision"], None)
        self.assertEqual(result["action"], "rungameid")
        self.assertEqual(result["launch_config_name"], None)

    def test_lutris_links_still_open(self):
        """Lutris.net's install buttons open lutris: links."""
        for url in ("lutris:quake", "lutris:rungameid/123"):
            result = parse_installer_url(url)
            self.assertEqual(result["game_slug"], "quake" if url == "lutris:quake" else "123")

    def test_other_schemes_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_installer_url("steam:rungameid/123")

    def test_action_rungame(self):
        result = parse_installer_url("qtris:rungame/quake")
        self.assertEqual(result["game_slug"], "quake")
        self.assertEqual(result["revision"], None)
        self.assertEqual(result["action"], "rungame")
        self.assertEqual(result["launch_config_name"], None)

    def test_action_rungame_launch_config(self):
        result = parse_installer_url("qtris:rungame/quake/OpenGL%20Edition")
        self.assertEqual(result["game_slug"], "quake")
        self.assertEqual(result["revision"], None)
        self.assertEqual(result["action"], "rungame")
        self.assertEqual(result["launch_config_name"], "OpenGL Edition")
