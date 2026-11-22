import io
import os
import tarfile
from pathlib import Path

import pytest

from qtris.delegates import InstallUIDelegate
from qtris.runners import NonInstallableRunnerError
from qtris.runners.runner import Runner


def test_extract_publishes_the_archive(tmp_path: Path) -> None:
    destination = tmp_path / "runner"
    download = tmp_path / "runner.tar.gz"
    with tarfile.open(download, "w:gz") as archive:
        for name in ("wrapper/runner", "wrapper/assets/data.bin"):
            data = b"fixture"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))

    Runner().extract(str(download), str(destination), executable="runner")

    assert os.access(destination / "runner", os.X_OK)
    assert (destination / "assets" / "data.bin").read_bytes() == b"fixture"
    assert not download.exists()


def test_a_runner_cannot_be_installed_unless_it_says_how() -> None:
    with pytest.raises(NonInstallableRunnerError):
        Runner().install(InstallUIDelegate())
