import os
from unittest import TestCase
from unittest.mock import Mock, patch

from qtris.installer.errors import ScriptingError
from qtris.installer.interpreter import ScriptInterpreter
from qtris.util.test_config import setup_test_environment

setup_test_environment()


class TestMonitorTaskReturnCode(TestCase):
    def setUp(self):
        installer = {
            "script": {"game": {"exe": "test"}},
            "version": "test",
            "game_slug": "test",
            "name": "test",
            "slug": "test",
            "runner": "linux",
        }
        self.interpreter = ScriptInterpreter(installer)
        iter_patch = patch.object(self.interpreter, "_iter_commands")
        self.iter_commands = iter_patch.start()
        self.addCleanup(iter_patch.stop)

    def monitor(self, return_code):
        command = Mock(is_running=False, return_code=return_code)
        return self.interpreter._monitor_task(command)

    def test_accepts_zero(self):
        self.assertFalse(self.monitor("0"))
        self.iter_commands.assert_called_once_with()

    def test_rejects_other_return_codes(self):
        for return_code in ("1", "256", "-9", ""):
            with self.subTest(return_code=return_code):
                with self.assertRaisesRegex(ScriptingError, "Command exited with code"):
                    self.monitor(return_code)
        self.iter_commands.assert_not_called()


class TestGogdlSetupPath(TestCase):
    """gogdl appends installDirectory to --path for downloads only; update and
    repair write into --path as given, so they must get the game directory."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.game_dir = os.path.join(self.tmp.name, "Some Game")
        os.makedirs(self.game_dir)
        os.makedirs(os.path.join(self.tmp.name, "pfx"))
        open(os.path.join(self.game_dir, "goggame-1.info"), "w", encoding="utf-8").close()
        installer = {
            "script": {"game": {"exe": "test"}},
            "version": "test",
            "game_slug": "test",
            "name": "test",
            "slug": "test",
            "runner": "wine",
        }
        self.interpreter = ScriptInterpreter(installer)
        self.interpreter.target_path = self.tmp.name
        self.interpreter.interpreter_ui_delegate = Mock()
        run_patch = patch("qtris.installer.commands.run_gogdl")
        self.run_gogdl = run_patch.start()
        self.addCleanup(run_patch.stop)
        post_patch = patch.object(ScriptInterpreter, "_apply_gogdl_post_install")
        self.post_install = post_patch.start()
        self.addCleanup(post_patch.stop)

    def test_download_targets_parent_directory(self):
        self.interpreter.gogdl_setup({"game_id": "1", "command": "download"})
        self.assertEqual(self.run_gogdl.call_args.kwargs["path"], self.tmp.name)
        self.post_install.assert_called_once_with(self.game_dir, "windows")

    def test_update_and_repair_target_game_directory(self):
        for command in ("update", "repair"):
            with self.subTest(command=command):
                self.interpreter.gogdl_setup({"game_id": "1", "command": command})
                self.assertEqual(self.run_gogdl.call_args.kwargs["path"], self.game_dir)
                self.post_install.assert_called_with(self.game_dir, "windows")

    def run_repair_or_update(self, command, nothing_to_do=False, broken_files=None):
        outcome = self.run_gogdl.return_value
        outcome.nothing_to_do = nothing_to_do
        outcome.broken_files = broken_files
        self.interpreter.gogdl_setup({"game_id": "1", "command": command})
        return self.interpreter.install_complete_text

    def test_completion_text_reports_outcome(self):
        self.assertIn("nothing needed repairing", self.run_repair_or_update("repair", nothing_to_do=True))
        self.assertIn("repaired: 3", self.run_repair_or_update("repair", broken_files=3))
        self.assertIn("were repaired", self.run_repair_or_update("repair"))
        self.assertIn("already up to date", self.run_repair_or_update("update", nothing_to_do=True))

    def test_completion_text_left_alone_after_real_update(self):
        self.assertIsNone(self.run_repair_or_update("update"))
