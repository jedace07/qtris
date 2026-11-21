"""Recognize ROMs by the file extensions only one system uses, and name them after their file."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

# The platform, in DEFAULT_INSTALLERS, of each extension that belongs to one system. Extensions
# several systems share, like .iso, .bin, .cue, .chd or .dsk, are left out
EXTENSION_PLATFORMS: dict[str, str] = {
    **dict.fromkeys((".nes", ".fds", ".unf", ".unif"), "nes"),
    **dict.fromkeys((".sfc", ".smc", ".fig", ".swc"), "snes"),
    **dict.fromkeys((".n64", ".z64", ".v64"), "n64"),
    **dict.fromkeys((".gb", ".gbc", ".sgb"), "gb"),
    ".gba": "gba",
    **dict.fromkeys((".nds", ".dsi"), "ds"),
    **dict.fromkeys((".3ds", ".cci", ".cxi"), "3ds"),
    **dict.fromkeys((".gcm", ".gcz", ".rvz"), "gamecube"),
    **dict.fromkeys((".wbfs", ".wad"), "wii"),
    **dict.fromkeys((".wua", ".wux", ".rpx"), "wiiu"),
    **dict.fromkeys((".nsp", ".xci"), "switch"),
    **dict.fromkeys((".vb", ".vboy"), "vb"),
    ".cso": "psp",
    ".vpk": "psvita",
    **dict.fromkeys((".md", ".gen", ".smd"), "md"),
    ".32x": "32x",
    ".sms": "sms",
    ".gg": "gg",
    **dict.fromkeys((".gdi", ".cdi"), "dc"),
    ".pce": "pce",
    **dict.fromkeys((".ngp", ".ngc"), "ngp"),
    ".ws": "wonderswan",
    ".wsc": "wonderswancolor",
    ".col": "colecovision",
    ".a26": "atari2600",
    **dict.fromkeys((".atr", ".atx", ".xfd"), "atari800"),
    ".lnx": "lynx",
    **dict.fromkeys((".j64", ".jag"), "jaguar"),
    **dict.fromkeys((".st", ".msa", ".stx"), "atari-st"),
    **dict.fromkeys((".adf", ".adz", ".dms"), "amiga"),
    **dict.fromkeys((".mx1", ".mx2"), "msx"),
    **dict.fromkeys((".ssd", ".dsd"), "bbc"),
    ".uef": "electron",
    ".cpr": "gx4000",
    ".tzx": "spectrumcass",
    ".prg": "c64prg",
    ".g64": "c64g64",
    **dict.fromkeys((".woz", ".nib", ".po"), "apple2"),
}

# The tags of ROM naming conventions, like (USA), (Rev 1) or [!]
_TAGS = re.compile(r"\s*(\([^)]*\)|\[[^\]]*\])")


def get_rom_platform(path: Path) -> str | None:
    """Return the platform of a ROM, or of the only file in a zip archive, from its extension."""
    suffix = path.suffix.casefold()
    if suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [info.filename for info in archive.infolist() if not info.is_dir()]
        if len(names) != 1:
            return None
        suffix = Path(names[0]).suffix.casefold()
    return EXTENSION_PLATFORMS.get(suffix)


def get_rom_name(path: Path) -> str:
    """Name a game after its ROM's file, without the tags of ROM naming conventions."""
    name = _TAGS.sub("", path.stem).replace("_", " ").strip()
    if name.endswith(", The"):
        name = "The " + name.removesuffix(", The")
    return name or path.stem
