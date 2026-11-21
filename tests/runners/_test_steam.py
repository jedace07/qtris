"""Tests for the Steam runner's reaper-based tracking and stopping of games."""

import signal
from contextlib import contextmanager
from unittest import TestCase
from unittest.mock import PropertyMock, patch

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
            with patch.object(steam, "get_steam_game_pids", return_value=[]):
                # game_thread still running -> always keep monitoring
                self.assertTrue(runner.keep_game_alive(set(), game_thread_running=True))

    def test_alive_while_reaper_present(self):
        with make_runner() as runner:
            with patch.object(steam, "get_steam_game_pids", return_value=[5555]):
                self.assertTrue(runner.keep_game_alive({5555}, game_thread_running=False))
            self.assertTrue(runner._steam_reaper_seen)

    def test_alive_during_launch_window_before_reaper(self):
        with make_runner() as runner:
            # Thread exited, reaper not up yet, never seen -> stay alive (grace)
            with patch.object(steam, "get_steam_game_pids", return_value=[]):
                self.assertTrue(runner.keep_game_alive(set(), game_thread_running=False))

    def test_quit_after_reaper_seen_then_gone(self):
        with make_runner() as runner:
            with patch.object(steam, "get_steam_game_pids", return_value=[5555]):
                runner.keep_game_alive({5555}, game_thread_running=False)  # sees reaper
            with patch.object(steam, "get_steam_game_pids", return_value=[]):
                # reaper gone after having been seen -> game genuinely quit
                self.assertFalse(runner.keep_game_alive(set(), game_thread_running=False))

    def test_quit_after_grace_expires(self):
        with make_runner() as runner:
            runner._launch_monitor_start = 0.0  # far in the past
            with (
                patch.object(steam, "get_steam_game_pids", return_value=[]),
                patch.object(steam.time, "monotonic", return_value=runner.launch_grace_seconds + 1),
            ):
                self.assertFalse(runner.keep_game_alive(set(), game_thread_running=False))

    def test_grace_window_uses_configured_value(self):
        # A custom launch_grace_seconds option overrides the default.
        with make_runner(runner_config={"launch_grace_seconds": "10"}) as runner:
            self.assertEqual(runner.launch_grace_seconds, 10)
            runner._launch_monitor_start = 0.0
            with (
                patch.object(steam, "get_steam_game_pids", return_value=[]),
                patch.object(steam.time, "monotonic", return_value=5),  # within 10s window
            ):
                self.assertTrue(runner.keep_game_alive(set(), game_thread_running=False))
            with (
                patch.object(steam, "get_steam_game_pids", return_value=[]),
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
            with patch.object(steam, "get_steam_game_pids", return_value=[]):
                self.assertFalse(runner.keep_game_alive(set(), game_thread_running=False))


def process(pid, ppid, cmdline):
    return host_processes.HostProcess(pid, ppid, cmdline)


STEAM = process(10, 1, "/home/me/.local/share/Steam/ubuntu12_32/steam")
REAPER = process(100, 10, "/home/me/.local/share/Steam/ubuntu12_32/reaper SteamLaunch AppId=440 -- /tf/hl2.sh")
GAME = process(101, 100, "/tf/hl2_linux -game tf")
GAME_CHILD = process(102, 101, "/tf/crashhandler")
OTHER_REAPER = process(200, 10, "/home/me/.local/share/Steam/ubuntu12_32/reaper SteamLaunch AppId=4400 -- /x")
OTHER_GAME = process(201, 200, "/x/game")


class ForceStopGameTest(TestCase):
    """Steam's reaper doesn't pass signals on, so force_stop_game stops the game's
    whole process tree, and never the Steam client or another game."""

    def test_stops_the_game_tree_and_waits_for_the_reaper(self):
        listings = [
            [STEAM, REAPER, GAME, GAME_CHILD, OTHER_REAPER, OTHER_GAME],
            [STEAM, OTHER_REAPER, OTHER_GAME],
        ]
        with make_runner() as runner:
            with (
                patch.object(host_processes, "list_processes", side_effect=listings),
                patch.object(host_processes, "send_signal") as send,
                patch.object(steam.time, "sleep"),
            ):
                runner.force_stop_game([])
        send.assert_called_once_with(signal.SIGTERM, {101, 102})

    def test_kills_what_ignores_sigterm(self):
        running = [STEAM, REAPER, GAME, GAME_CHILD]
        clock = iter(range(100))
        with make_runner() as runner:
            with (
                patch.object(host_processes, "list_processes", return_value=running),
                patch.object(host_processes, "send_signal") as send,
                patch.object(steam.time, "sleep"),
                patch.object(steam.time, "monotonic", side_effect=lambda: next(clock)),
            ):
                runner.force_stop_game([])
        self.assertEqual(send.call_args_list[-1].args, (signal.SIGKILL, {100, 101, 102}))

    def test_nothing_to_stop(self):
        with make_runner() as runner:
            with (
                patch.object(host_processes, "list_processes", return_value=[STEAM, OTHER_REAPER, OTHER_GAME]),
                patch.object(host_processes, "send_signal") as send,
            ):
                runner.force_stop_game([])
        send.assert_not_called()


class ReaperTest(TestCase):
    def test_matches_only_the_reaper_of_the_appid(self):
        processes = [STEAM, REAPER, GAME, OTHER_REAPER]
        self.assertEqual(steam.find_reapers("440", processes), [100])
        self.assertEqual(steam.find_reapers("", processes), [])

    def test_a_process_that_mentions_the_reaper_is_not_one(self):
        self.assertFalse(steam.is_reaper_for("bash -c run reaper SteamLaunch AppId=440", "440"))
        self.assertFalse(steam.is_reaper_for("steamwebhelper SteamLaunch AppId=440", "440"))
        self.assertTrue(steam.is_reaper_for("reaper SteamLaunch AppId=440", "440"))


class HostProcessesTest(TestCase):
    def test_parses_ps_output(self):
        output = "    1     0 /sbin/init splash\n  100    10 /Steam/reaper SteamLaunch AppId=440 -- x\n  7  1\n"
        self.assertEqual(
            list(host_processes.parse_ps_output(output)),
            [
                process(1, 0, "/sbin/init splash"),
                process(100, 10, "/Steam/reaper SteamLaunch AppId=440 -- x"),
                process(7, 1, ""),
            ],
        )

    def test_descendants(self):
        processes = [STEAM, REAPER, GAME, GAME_CHILD, OTHER_REAPER, OTHER_GAME]
        self.assertEqual(host_processes.get_descendants([100], processes), {101, 102})
        self.assertEqual(host_processes.get_descendants([10], processes), {100, 101, 102, 200, 201})

    def test_sandbox_signals_through_flatpak_spawn(self):
        with (
            patch.object(host_processes, "_host_command", return_value=["flatpak-spawn", "--host"]),
            patch.object(host_processes.subprocess, "run") as run,
        ):
            host_processes.send_signal(signal.SIGTERM, [102, 101])
        self.assertEqual(run.call_args.args[0], ["flatpak-spawn", "--host", "kill", "-s", "TERM", "--", "101", "102"])
