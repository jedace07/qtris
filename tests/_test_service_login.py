"""Logging in to services, and runner actions, ask the user through delegates."""

from unittest.mock import MagicMock, patch

import pytest

from qtris import settings
from qtris.delegates import Delegate, InstallUIDelegate, ServiceLoginUIDelegate
from qtris.services.base import SERVICE_LOGIN, OnlineService
from qtris.services.itchio import ItchIoService


class RecordingLoginDelegate(ServiceLoginUIDelegate):
    """Answers with canned replies and records what it was asked."""

    def __init__(self, answer=False, text=None, file=None):
        self.answer = answer
        self.text = text
        self.file = file
        self.calls = []

    def ask_question(self, title, question_markup):
        self.calls.append("ask_question")
        return self.answer

    def ask_for_text(self, title, question_markup, initial_value=""):
        self.calls.append("ask_for_text")
        return self.text

    def ask_for_file(self, message, default_path=None):
        self.calls.append("ask_for_file")
        return self.file

    def show_web_login(self, service):
        self.calls.append("show_web_login")

    def install_service_launcher(self, service):
        self.calls.append("install_service_launcher")


@pytest.fixture
def logins(fake_main_loop):
    """The services SERVICE_LOGIN fired for."""
    logged_in = []
    registration = SERVICE_LOGIN.register(logged_in.append)
    yield logged_in
    registration.unregister()


def test_itch_io_saves_the_api_key_the_user_gives(tmp_path, monkeypatch, fake_main_loop, logins):
    service = ItchIoService()
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(service, "api_key_path", str(tmp_path / "api-key"))
    monkeypatch.setattr(service, "validate_api_key", lambda key: key == "secret-key")

    service.login(RecordingLoginDelegate(text="secret-key"))
    fake_main_loop.run_pending()

    assert service.credentials.read()["api_key"] == "secret-key"
    assert service.credentials.path.stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "api-key").exists()
    assert logins == [service]


def test_itch_io_preserves_the_saved_key_when_the_user_cancels(tmp_path, monkeypatch, fake_main_loop, logins):
    service = ItchIoService()
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    legacy_path = tmp_path / "api-key"
    legacy_path.write_text("existing-key", encoding="utf-8")
    monkeypatch.setattr(service, "api_key_path", str(legacy_path))

    service.login(RecordingLoginDelegate(text=None))
    fake_main_loop.run_pending()

    assert service.get_headers() == {"Authorization": "Bearer existing-key"}
    assert not legacy_path.exists()
    assert logins == []


def test_service_with_a_launcher_installs_it_before_logging_in(monkeypatch):
    service = OnlineService()
    monkeypatch.setattr(service, "client_installer", "some-launcher")
    monkeypatch.setattr(service, "is_launcher_installed", lambda: False)
    delegate = RecordingLoginDelegate()

    service.login(delegate)

    assert delegate.calls == ["install_service_launcher"]


def test_default_delegates_never_wait_for_a_user():
    delegate = ServiceLoginUIDelegate()
    assert delegate.ask_question("Title", "Really?") is False
    assert delegate.ask_for_text("Title", "Name?") is None
    assert delegate.ask_for_file("Pick one") is None
    delegate.show_notice("Hello")
    delegate.show_web_login(MagicMock())
    # The installer's own yes/no question still defaults to yes
    assert InstallUIDelegate().show_install_yesno_inquiry("Install?", "Title") is True


def test_wine_runs_the_executable_the_user_picks():
    from qtris.runners.wine import wine

    runner = wine()
    delegate = RecordingLoginDelegate(file="/games/setup.exe")
    with patch.object(runner, "prelaunch"), patch.object(runner, "_run_executable") as run_executable:
        runner.run_wineexec(delegate)

    run_executable.assert_called_once_with("/games/setup.exe")


def test_wine_runs_nothing_when_the_user_cancels():
    from qtris.runners.wine import wine

    runner = wine()
    with patch.object(runner, "_run_executable") as run_executable:
        runner.run_wineexec(Delegate())

    run_executable.assert_not_called()
