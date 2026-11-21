from unittest import TestCase

from qtris.uri import parse_installer_url


class TestInstallerUrls(TestCase):
    def test_legacy_url(self):
        result = parse_installer_url("qtris:quake")
        self.assertEqual(result["game_slug"], "quake")
        self.assertEqual(result["action"], None)
        self.assertEqual(result["launch_config_name"], None)

    def test_action_rungameid(self):
        result = parse_installer_url("qtris:rungameid/123")
        self.assertEqual(result["game_slug"], "123")
        self.assertEqual(result["action"], "rungameid")
        self.assertEqual(result["launch_config_name"], None)

    def test_other_schemes_are_rejected(self):
        for url in ("lutris:rungameid/123", "steam:rungameid/123"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_installer_url(url)

    def test_action_rungame(self):
        result = parse_installer_url("qtris:rungame/quake")
        self.assertEqual(result["game_slug"], "quake")
        self.assertEqual(result["action"], "rungame")
        self.assertEqual(result["launch_config_name"], None)

    def test_action_rungame_launch_config(self):
        result = parse_installer_url("qtris:rungame/quake/OpenGL%20Edition")
        self.assertEqual(result["game_slug"], "quake")
        self.assertEqual(result["action"], "rungame")
        self.assertEqual(result["launch_config_name"], "OpenGL Edition")
