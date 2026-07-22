from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_provider_factory_uses_concrete_maf_distributions():
    source = read("app/provider_factory.py")

    assert "from agent_framework_foundry import FoundryChatClient" in source
    assert "from agent_framework_openai import OpenAIChatClient, OpenAIChatCompletionClient" in source
    assert "from agent_framework.foundry import" not in source
    assert "from agent_framework.openai import" not in source


def test_windows_specs_produce_onefile_and_compatibility_onedir():
    onefile = read("mcpclient_win.spec")
    onedir = read("mcpclient_win_onedir.spec")

    for source in (onefile, onedir):
        assert "'agent_framework_foundry'" in source
        assert "'agent_framework_openai'" in source
        assert "name.lower() == 'ucrtbase.dll'" in source
        assert "name.lower().startswith('api-ms-win-')" in source
        assert "'keyring'" in source
        assert "'cryptography'" in source
        assert "'keyring.backends.Windows'" in source
        assert "'pypdf'" in source

    assert "onefile=True" in onefile
    assert "COLLECT(" not in onefile
    assert "COLLECT(" in onedir
    assert "name='mcpclient-onedir'" in onedir


def test_macos_bundle_uses_three_part_short_and_four_part_build_versions():
    source = read("mcpclient_mac.spec")

    assert "SHORT_VERSION = '.'.join(VERSION.split('.')[:3])" in source
    assert "'CFBundleShortVersionString': SHORT_VERSION" in source
    assert "'CFBundleVersion': VERSION" in source
    assert "'keyring.backends.macOS'" in source
    assert "'pypdf'" in source


def test_windowed_smoke_writes_traceback_instead_of_showing_unreadable_modal():
    source = read("app_runner.py")

    assert 'parser.add_argument("--smoke-test"' in source
    assert "MCPCLIENT_SMOKE_REPORT" in source
    assert "traceback.format_exc()" in source
    assert "os._exit(1)" in source
    assert "SecretProtector" in source
    assert "import keyring" in source
    assert 'encrypt("package-smoke")' in source
    assert "set_password(_smoke_service" in source
    assert "get_password(_smoke_service" in source
    assert "delete_password(_smoke_service" in source

    runner = read("scripts/smoke_package.py")
    assert '"taskkill", "/PID"' in runner
    assert "os.killpg" in runner


def test_release_workflow_packages_both_windows_variants():
    source = read(".github/workflows/build-release.yml")

    assert "mcpclient_win.spec" in source
    assert "mcpclient_win_onedir.spec" in source
    assert "mcpclient-windows-onedir-${{ needs.version.outputs.version }}.zip" in source
    assert "python scripts/smoke_package.py dist/mcpclient.exe" in source
    assert "python scripts/smoke_package.py dist/mcpclient-onedir/mcpclient.exe" in source
    assert "python scripts/version.py verify --newer-than" in source
    assert "Tag v${version} already exists" in source
    assert "SHA256SUMS.txt" in source
    assert "sha256sum --check SHA256SUMS-windows.txt" in source
    assert './scripts/package_windows.ps1 -Version "${{ needs.version.outputs.version }}"' in source


def test_windows_diagnostic_workflow_does_not_create_a_release():
    source = read(".github/workflows/build-release-win-exe.yml")

    assert "actions/upload-artifact@" in source
    assert "gh release create" not in source
    assert "contents: write" not in source
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1" in source


def test_workflows_use_current_immutable_action_releases():
    sources = "\n".join(
        read(path)
        for path in (
            ".github/workflows/build-release.yml",
            ".github/workflows/build-release-win-exe.yml",
            ".github/workflows/container-release.yml",
            ".github/workflows/ci.yml",
        )
    )

    assert "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0" in sources
    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0" in sources
    assert "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0" in sources
    assert "node-version: '20'" not in sources


def test_windows_packager_validates_archive_roots_and_checksums():
    source = read("scripts/package_windows.ps1")

    assert "Assert-ArchiveEntry" in source
    assert "mcpclient-onedir/mcpclient.exe" in source
    assert "SHA256SUMS-windows.txt" in source
    assert "version.py" in source


def test_docker_and_ci_use_supported_node_lts():
    assert "FROM node:24-bookworm-slim" in read("Dockerfile")
    assert "node-version: '24'" in read(".github/workflows/ci.yml")


def test_secret_protection_dependencies_and_container_contract_are_explicit():
    assert "cryptography==49.0.0" in read("requirements.txt")
    assert "keyring==25.7.0" in read("requirements-desktop.txt")
    assert "pypdf==6.14.2" in read("requirements.txt")
    dockerfile = read("Dockerfile")
    assert "MCPCLIENT_ENCRYPTION_KEY" in dockerfile
    assert "Never bake it" in dockerfile
    workflow = read(".github/workflows/container-release.yml")
    assert "smoke_container_encryption.py" in workflow
    assert "/app/smoke.py" in workflow
    assert "unexpectedly loaded without MCPCLIENT_ENCRYPTION_KEY" in workflow
    assert "unexpectedly loaded with a different key" in workflow


def test_release_uses_version_source_once_and_publishes_checksums():
    release = read(".github/workflows/build-release.yml")
    container = read(".github/workflows/container-release.yml")

    assert "python scripts/version.py verify --newer-than" in release
    assert 'git ls-remote --exit-code --tags origin "refs/tags/v${version}"' in release
    assert "sha256sum" in release
    assert "--target \"$GITHUB_SHA\"" in release
    assert "ghcr.io/${{ steps.repo.outputs.repo }}:${{ steps.version.outputs.version }}" in container
    assert "target: runtime-prebuilt" in container
