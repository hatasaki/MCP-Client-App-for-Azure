from __future__ import annotations

import io
import json
import stat
import zipfile
from pathlib import Path

import pytest
from agent_framework import AgentSession, Message, SessionContext

from app.skills_manager import SkillLibraryError, SkillsManager


def skill_md(name: str, description: str = "Use this skill for tests.") -> bytes:
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n"
        f"# {name}\nFollow these instructions.\n"
    ).encode()


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue()


@pytest.mark.asyncio
async def test_upload_standalone_skill_md_and_create_maf_provider(tmp_path: Path):
    manager = SkillsManager(tmp_path)

    uploaded = await manager.upload("SKILL.md", skill_md("writing-guide"))

    assert uploaded[0]["id"] == "writing-guide"
    assert manager.list()[0]["resourceCount"] == 0
    provider = manager.create_provider(["writing-guide"])
    assert provider is not None

    class AgentStub:
        name = "test"

    context = SessionContext(session_id="chat", input_messages=[Message("user", ["hello"])])
    await provider.before_run(
        agent=AgentStub(),  # type: ignore[arg-type]
        session=AgentSession(session_id="chat"),
        context=context,
        state={},
    )
    assert "writing-guide" in "\n".join(context.instructions)
    tools = {tool.name: tool for tool in context.tools}
    assert set(tools) >= {"load_skill", "read_skill_resource"}
    assert "run_skill_script" not in tools
    assert tools["load_skill"].approval_mode == "never_require"
    assert tools["read_skill_resource"].approval_mode == "never_require"


@pytest.mark.asyncio
async def test_zip_bundle_installs_multiple_skills_and_removes_scripts(tmp_path: Path):
    manager = SkillsManager(tmp_path)
    bundle = zip_bytes({
        "bundle/alpha/SKILL.md": skill_md("alpha"),
        "bundle/alpha/references/guide.txt": b"alpha guide",
        "bundle/alpha/LICENSE.txt": b"MIT",
        "bundle/alpha/scripts/danger.py": b"raise SystemExit('must not run')",
        "bundle/alpha/scripts/nested/danger.bin": b"arbitrary code bytes",
        "bundle/beta/SKILL.md": skill_md("beta"),
        "bundle/beta/assets/template.md": b"# Template",
    })

    uploaded = await manager.upload("skills.zip", bundle)

    assert [item["id"] for item in uploaded] == ["alpha", "beta"]
    assert uploaded[0]["scriptsIgnored"] is True
    assert not (tmp_path / "library" / "alpha" / "scripts").exists()
    assert (tmp_path / "library" / "alpha" / "LICENSE.txt").is_file()
    provider = manager.create_provider(["alpha", "beta"])
    assert provider is not None
    assert "danger.py" not in str(manager.list())


@pytest.mark.asyncio
async def test_zip_with_root_skill_is_normalized_to_official_directory_layout(tmp_path: Path):
    manager = SkillsManager(tmp_path)
    bundle = zip_bytes({
        "SKILL.md": skill_md("root-skill"),
        "references/guide.md": b"# Guide",
    })

    uploaded = await manager.upload("root-skill.zip", bundle)

    assert uploaded[0]["id"] == "root-skill"
    assert (tmp_path / "library" / "root-skill" / "SKILL.md").is_file()
    assert (tmp_path / "library" / "root-skill" / "references" / "guide.md").is_file()


@pytest.mark.asyncio
async def test_maf_validates_multiline_description_and_unix_regular_zip_mode(tmp_path: Path):
    manager = SkillsManager(tmp_path)
    content = (
        "---\nname: multiline-skill\ndescription: >\n"
        "  Use this skill for multiline\n  description tests.\n---\n# Instructions\nUse it.\n"
    ).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        info = zipfile.ZipInfo("multiline-skill/SKILL.md")
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(info, content)

    uploaded = await manager.upload("unix.zip", stream.getvalue())

    assert uploaded[0]["description"] == "Use this skill for multiline description tests."


@pytest.mark.asyncio
async def test_upload_rejects_zip_slip_and_duplicate_casefolded_paths(tmp_path: Path):
    manager = SkillsManager(tmp_path)
    traversal = zip_bytes({"../evil/SKILL.md": skill_md("evil")})
    with pytest.raises(SkillLibraryError, match="Unsafe ZIP path"):
        await manager.upload("evil.zip", traversal)

    duplicate = zip_bytes({
        "alpha/SKILL.md": skill_md("alpha"),
        "ALPHA/skill.md": skill_md("alpha"),
    })
    with pytest.raises(SkillLibraryError, match="duplicate path"):
        await manager.upload("duplicate.zip", duplicate)

    ads = zip_bytes({"alpha/SKILL.md:payload": skill_md("alpha")})
    with pytest.raises(SkillLibraryError, match="Unsafe ZIP path"):
        await manager.upload("ads.zip", ads)


@pytest.mark.asyncio
async def test_upload_rejects_invalid_directory_name_and_unsupported_resource(tmp_path: Path):
    manager = SkillsManager(tmp_path)
    mismatch = zip_bytes({"wrong/SKILL.md": skill_md("right")})
    with pytest.raises(SkillLibraryError, match="must match"):
        await manager.upload("mismatch.zip", mismatch)

    binary = zip_bytes({
        "alpha/SKILL.md": skill_md("alpha"),
        "alpha/assets/payload.exe": b"MZ",
    })
    with pytest.raises(SkillLibraryError, match="Unsupported skill resource type"):
        await manager.upload("binary.zip", binary)

    with pytest.raises(SkillLibraryError, match="reserved by Windows"):
        await manager.upload("SKILL.md", skill_md("con"))


@pytest.mark.asyncio
async def test_replacement_manifest_integrity_and_deletion(tmp_path: Path):
    manager = SkillsManager(tmp_path)
    first = await manager.upload("SKILL.md", skill_md("alpha", "First description"))
    first_hash = first[0]["contentHash"]
    second = await manager.upload("SKILL.md", skill_md("alpha", "Second description"))

    assert second[0]["contentHash"] != first_hash
    reloaded = SkillsManager(tmp_path)
    assert reloaded.list()[0]["description"] == "Second description"
    assert json.loads((tmp_path / "skills.json").read_text(encoding="utf-8"))["schemaVersion"] == 1

    await reloaded.delete("alpha")
    assert reloaded.list() == []
    assert not (tmp_path / "library" / "alpha").exists()


@pytest.mark.asyncio
async def test_unknown_or_duplicate_selection_is_rejected(tmp_path: Path):
    manager = SkillsManager(tmp_path)
    with pytest.raises(SkillLibraryError, match="not found"):
        manager.create_provider(["missing"])
    await manager.upload("SKILL.md", skill_md("same"))
    with pytest.raises(SkillLibraryError, match="Duplicate"):
        manager.fingerprint(["same", "same"])
    with pytest.raises(SkillLibraryError, match="at most 20"):
        manager.fingerprint([f"skill-{index}" for index in range(21)])
