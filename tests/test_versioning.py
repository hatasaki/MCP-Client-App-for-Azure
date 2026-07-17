from __future__ import annotations

from pathlib import Path

import pytest

from scripts.version import VersionError, current_version, format_version, parse_version, set_version

ROOT = Path(__file__).resolve().parents[1]


def test_repository_version_file_is_consistent():
    version = current_version(ROOT / "version_info.txt")
    assert len(version) == 4
    assert all(0 <= part <= 65535 for part in version)


def test_set_version_updates_all_fields_atomically(tmp_path: Path):
    target = tmp_path / "version_info.txt"
    target.write_text((ROOT / "version_info.txt").read_text(encoding="utf-8"), encoding="utf-8")

    assert set_version("1.2.3.4", target) == (1, 2, 3, 4)
    text = target.read_text(encoding="utf-8")

    assert "filevers=(1, 2, 3, 4)" in text
    assert "prodvers=(1, 2, 3, 4)" in text
    assert "StringStruct('FileVersion', '1.2.3.4')" in text
    assert "StringStruct('ProductVersion', '1.2.3.4')" in text
    assert current_version(target) == (1, 2, 3, 4)


def test_inconsistent_version_file_is_rejected(tmp_path: Path):
    target = tmp_path / "version_info.txt"
    text = (ROOT / "version_info.txt").read_text(encoding="utf-8")
    current = format_version(current_version(ROOT / "version_info.txt"))
    target.write_text(
        text.replace(
            f"StringStruct('FileVersion', '{current}')",
            "StringStruct('FileVersion', '9.9.9.9')",
        ),
        encoding="utf-8",
    )

    with pytest.raises(VersionError, match="inconsistent"):
        current_version(target)


@pytest.mark.parametrize("value", ["1.2.3", "1.2.3.4.5", "01.2.3.4", "1.2.x.4", "1.2.3.65536"])
def test_invalid_release_versions_are_rejected(value: str):
    with pytest.raises(VersionError):
        parse_version(value)
