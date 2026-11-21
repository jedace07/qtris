"""Tests for the Steam runner's reaper-based tracking and stopping of games."""

import os
import signal
import subprocess
from contextlib import contextmanager
from unittest import TestCase
from unittest.mock import Mock, PropertyMock, patch

from qtris.util.test_config import setup_test_environment

setup_test_environment()

from qtris.runners import steam  # noqa: E402  (must follow setup_test_environment)
from qtris.util import host_processes  # noqa: E402


@contextmanager
def make_runner(appid="440", runner_config=None):
    """Yield a Steam runner built through its real __init__ (so new instance
    state can't be silently missed by the fixture), with config access stubbed
    so no real config/DB/disk is touched.

    appid and runner_config are exposed via patched property reads.
    """
    runner = steam.steam()  # real __init__, config=None
    game_config = {"appid": appid}
    with (
        patch.object(steam.steam, "game_config", new_callable=PropertyMock, return_value=game_config),
        patch.object(
            steam.steam,
            "runner_config",
            new_callable=PropertyMock,
            return_value=runner_config or {},
        ),
    ):
        yield runner


class KeepGameAliveTest(TestCase):
    """beat() asks keep_game_alive() whether to keep monitoring even though the
    launcher thread has exited -- covering the launch window and game lifetime."""

    def test_alive_while_thread_running(self):
        with make_runner() as runner:
            with patch.object(steam, "get_steam_game_pids", return_value=set()):
                # game_thread still running -> always keep monitoring
                self.assertTrue(runner.keep_game_alive(set(), game_thread_running=True))

    def test_alive_while_reaper_present(self):
        with make_runner() as runner:
            with patch.object(steam, "get_steam_game_pids", return_value={5555}):
                self.assertTrue(runner.keep_game_alive({5555}, game_thread_running=False))
            self.assertTrue(runner._steam_game_seen)

    def test_alive_during_launch_window_before_reaper(self):
        with make_runner() as runner:
            # Thread exited, reaper not up yet, never seen -> stay alive (grace)
            with patch.object(steam, "get_steam_game_pids", return_value=set()):
                self.assertTrue(runner.keep_game_alive(set(), game_thread_running=False))

    def test_quit_after_reaper_seen_then_gone(self):
        with make_runner() as runner:
            with patch.object(steam, "get_steam_game_pids", return_value={5555}):
                runner.keep_game_alive({5555}, game_thread_running=False)  # sees reaper
            with patch.object(steam, "get_steam_game_pids", return_value=set()):
                # reaper gone after having been seen -> game genuinely quit
                self.assertFalse(runner.keep_game_alive(set(), game_thread_running=False))

    def test_quit_after_grace_expires(self):
        with make_runner() as runner:
            runner._launch_monitor_start = 0.0  # far in the past
            with (
                patch.object(steam, "get_steam_game_pids", return_value=set()),
                patch.object(steam.time, "monotonic", return_value=runner.launch_grace_seconds + 1),
            ):
                self.assertFalse(runner.keep_game_alive(set(), game_thread_running=False))

    def test_grace_window_uses_configured_value(self):
        # A custom launch_grace_seconds option overrides the default.
        with make_runner(runner_config={"launch_grace_seconds": "10"}) as runner:
            self.assertEqual(runner.launch_grace_seconds, 10)
            runner._launch_monitor_start = 0.0
            with (
                patch.object(steam, "get_steam_game_pids", return_value=set()),
                patch.object(steam.time, "monotonic", return_value=5),  # within 10s window
            ):
                self.assertTrue(runner.keep_game_alive(set(), game_thread_running=False))
            with (
                patch.object(steam, "get_steam_game_pids", return_value=set()),
                patch.object(steam.time, "monotonic", return_value=11),  # past 10s window
            ):
                self.assertFalse(runner.keep_game_alive(set(), game_thread_running=False))

    def test_grace_default_and_invalid_fallback(self):
        with make_runner() as runner:  # no option set
            self.assertEqual(runner.launch_grace_seconds, steam.DEFAULT_LAUNCH_GRACE_SECONDS)
        # Non-numeric and non-positive values both fall back to the default and
        # both emit a warning (the two invalid cases are treated identically).
        with make_runner(runner_config={"launch_grace_seconds": "notanumber"}) as runner:
            with self.assertLogs(steam.logger, level="WARNING"):
                self.assertEqual(runner.launch_grace_seconds, steam.DEFAULT_LAUNCH_GRACE_SECONDS)
        with make_runner(runner_config={"launch_grace_seconds": "-5"}) as runner:
            with self.assertLogs(steam.logger, level="WARNING"):
                self.assertEqual(runner.launch_grace_seconds, steam.DEFAULT_LAUNCH_GRACE_SECONDS)

    def test_false_without_appid(self):
        with make_runner(appid="") as runner:
            self.assertFalse(runner.keep_game_alive({5555}, game_thread_running=False))

    def test_drm_free_mode_is_tracked_like_any_game(self):
        config = {"appid": "440", "run_without_steam": True, "steamless_binary": "/games/tf/hl2.sh"}
        with make_runner() as runner, patch.object(steam.steam, "game_config", new_callable=PropertyMock) as game:
            game.return_value = config
            with patch.object(steam, "get_steam_game_pids", return_value=set()):
                self.assertFalse(runner.keep_game_alive(set(), game_thread_running=False))


class ForceStopGameTest(TestCase):
    """force_stop_game stops every process carrying the game's appid, wherever it
    runs, and kills what ignores SIGTERM."""

    def test_stops_the_game_and_waits_for_it(self):
        with make_runner() as runner:
            with (
                patch.object(steam, "get_steam_game_pids", side_effect=[{100, 101}, set()]),
                patch.object(host_processes, "send_signal") as send,
                patch.object(steam.time, "sleep"),
            ):
                runner.force_stop_game([])
        send.assert_called_once_with(signal.SIGTERM, {100, 101})

    def test_kills_what_ignores_sigterm(self):
        clock = iter(range(100))
        with make_runner() as runner:
            with (
                patch.object(steam, "get_steam_game_pids", return_value={100, 101}),
                patch.object(host_processes, "send_signal") as send,
                patch.object(steam.time, "sleep"),
                patch.object(steam.time, "monotonic", side_effect=lambda: next(clock)),
            ):
                runner.force_stop_game([])
        self.assertEqual(send.call_args_list[-1].args, (signal.SIGKILL, {100, 101}))

    def test_nothing_to_stop(self):
        with make_runner() as runner:
            with (
                patch.object(steam, "get_steam_game_pids", return_value=set()),
                patch.object(host_processes, "send_signal") as send,
            ):
                runner.force_stop_game([])
        send.assert_not_called()

    def test_looks_for_both_variables_steam_sets(self):
        with patch.object(host_processes, "find_by_environment", return_value={7}) as find:
            self.assertEqual(steam.get_steam_game_pids("440"), {7})
        find.assert_called_once_with(["SteamAppId=440", "SteamGameId=440"])
        self.assertEqual(steam.get_steam_game_pids(""), set())


class HostProcessesTest(TestCase):
    def test_finds_processes_by_an_exact_environment_entry(self):
        def start(appid):
            env = {**os.environ, "SteamAppId": appid}
            process = subprocess.Popen(["sleep", "30"], env=env)
            self.addCleanup(process.wait)
            self.addCleanup(process.kill)
            return process.pid

        game = start("4400440")
        other = start("44004400")
        with patch.object(host_processes, "_host_command", return_value=set()):
            found = host_processes.find_by_environment(["SteamAppId=4400440"])
        self.assertIn(game, found)
        self.assertNotIn(other, found)

    def test_sandbox_searches_and_signals_through_flatpak_spawn(self):
        output = "/proc/12/environ\n/proc/345/environ\n"
        with (
            patch.object(host_processes, "_host_command", return_value=["flatpak-spawn", "--host"]),
            patch.object(host_processes.subprocess, "run", return_value=Mock(stdout=output)) as run,
        ):
            self.assertEqual(host_processes.find_by_environment(["SteamAppId=440"]), {12, 345})
            command = run.call_args.args[0]
            self.assertEqual(command[:3], ["flatpak-spawn", "--host", "sh"])
            self.assertEqual(command[-2:], ["-e", "SteamAppId=440"])
            host_processes.send_signal(signal.SIGTERM, [102, 101])
        self.assertEqual(run.call_args.args[0], ["flatpak-spawn", "--host", "kill", "-s", "TERM", "--", "101", "102"])


class SteamGamePlatformTest(TestCase):
    """A Steam game's platform is that of the build Steam runs: Windows through Proton."""

    def get_platform(self, oslist=(), compat_tools=None):
        with (
            patch.object(steam, "get_app_oslist", return_value=tuple(oslist)),
            patch.object(steam, "get_compat_tools", return_value=compat_tools or {}),
        ):
            return steam.get_steam_game_platform("440")

    def test_native_games_are_linux(self):
        self.assertEqual(self.get_platform(["windows", "macos", "linux"]), "Linux")

    def test_games_without_a_linux_build_are_windows(self):
        self.assertEqual(self.get_platform(["windows"]), "Windows")
        # The default tool for those games doesn't affect native ones
        self.assertEqual(self.get_platform(["windows", "linux"], {"0": "proton_experimental"}), "Linux")

    def test_native_games_forced_through_proton_are_windows(self):
        self.assertEqual(self.get_platform(["windows", "linux"], {"440": "Proton-GE Latest"}), "Windows")

    def test_native_games_forced_into_the_steam_linux_runtime_stay_linux(self):
        self.assertEqual(self.get_platform(["windows", "linux"], {"440": "steamlinuxruntime"}), "Linux")

    def test_games_steam_does_not_know_have_no_platform(self):
        self.assertEqual(self.get_platform(), "")
        self.assertEqual(steam.get_steam_game_platform(""), "")

    def test_the_runner_reports_its_games_platform(self):
        with (
            make_runner(appid="1422450") as runner,
            patch.object(steam, "get_steam_game_platform", return_value="Windows") as get_platform,
        ):
            self.assertEqual(runner.get_platform(), "Windows")
        get_platform.assert_called_once_with("1422450")
