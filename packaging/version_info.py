"""The Windows version resource both specs give their exe: Properties > Details (D71).

A PyInstaller exe without one looks like a throwaway build, which is one of the few
features Defender's machine-learning verdicts are known to weigh; two projects cleared the
same Bearfoos.A!ml false positive by adding one (docs/research). Imported by the specs,
which PyInstaller runs with this folder as ``SPECPATH``.
"""

from __future__ import annotations

import json
from pathlib import Path


def version_resource(root: Path, *, name: str, description: str) -> str:
    """Write ``build/<name>_version_info.txt`` and return its path, for ``EXE(version=…)``."""
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    info_file = root / "app" / "build_info.json"
    info = json.loads(info_file.read_text(encoding="utf-8")) if info_file.exists() else {}
    version = str(info.get("version", "0.0.0"))
    numbers = tuple((list(map(int, version.split(".")[:3])) + [0, 0, 0, 0])[:4])
    commit = str(info.get("commit", "unknown"))
    resource = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "AM Consulting"),
                            StringStruct("FileDescription", description),
                            StringStruct("FileVersion", version),
                            StringStruct("InternalName", name),
                            StringStruct("LegalCopyright", "(c) AM Consulting"),
                            StringStruct("OriginalFilename", f"{name}.exe"),
                            StringStruct("ProductName", "Upshot"),
                            StringStruct("ProductVersion", f"{version} ({commit})"),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )
    target = root / "build" / f"{name}_version_info.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(resource), encoding="utf-8")
    return str(target)
