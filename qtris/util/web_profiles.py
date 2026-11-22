"""Storage paths and process locks for web game profiles."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


def profile_directory(root: str, game_id: str | int, installed_at: int | None) -> Path:
    # SQLite can reuse deleted IDs, so include the time the game was added
    return Path(root) / "web-profiles" / f"{int(game_id)}-{int(installed_at or 0)}"


@contextmanager
def lock_profile(directory: Path, *, create: bool = True) -> Generator[bool]:
    """Claim the profile, yielding False if a player or removal already has it."""
    if create:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(directory / "player.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(descriptor)
