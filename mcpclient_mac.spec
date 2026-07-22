# -*- mode: python ; coding: utf-8 -*-
"""macOS bundle build using version_info.txt."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata
from scripts.version import current_version, format_version

_version_txt = Path('version_info.txt')
VERSION = format_version(current_version(_version_txt))
SHORT_VERSION = '.'.join(VERSION.split('.')[:3])

_packages = [
    'agent_framework',
    'agent_framework_foundry',
    'agent_framework_openai',
    'agent_framework_anthropic',
    'mcp',
    'openai',
    'anthropic',
    'pypdf',
    'azure.ai.projects',
    'azure.ai.inference',
    'azure.identity',
]
hiddenimports = [
    'backend.main',
    'keyring',
    'keyring.backends.macOS',
    'engineio.async_drivers.asgi',
    'uvicorn.logging',
    'uvicorn.loops.auto',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets.auto',
]
for package in _packages:
    if package == 'mcp':
        hiddenimports += collect_submodules(
            package,
            filter=lambda name: not name.startswith('mcp.cli'),
            on_error='warn',
        )
    elif package == 'openai':
        hiddenimports += collect_submodules(
            package,
            filter=lambda name: not name.startswith('openai.helpers'),
            on_error='warn',
        )
    else:
        hiddenimports += collect_submodules(package, on_error='warn')

datas = [
    ('client/build', 'client/build'),
    ('assets/loading.html', 'assets'),
    ('assets/icon.ico', 'assets'),
]
for distribution in [
    'agent-framework-core',
    'agent-framework-openai',
    'agent-framework-foundry',
    'agent-framework-anthropic',
    'mcp',
    'openai',
    'anthropic',
    'pypdf',
    'azure-ai-projects',
    'azure-ai-inference',
    'azure-identity',
    'cryptography',
    'keyring',
]:
    datas += copy_metadata(distribution)

a = Analysis(['app_runner.py'],
             pathex=[],
             binaries=[],
             datas=datas,
             hiddenimports=hiddenimports,
             hookspath=[],
             hooksconfig={},
             runtime_hooks=[],
             excludes=[],
             noarchive=False,
             optimize=0)
pyz = PYZ(a.pure)

exe = EXE(pyz,
          a.scripts,
          exclude_binaries=True,
          name='mcpclient_bin',
          console=False,
          version='version_info.txt',
          icon=['assets/icon.icns'])

coll = COLLECT(exe,
               a.binaries,
               a.datas,
               strip=False,
               upx=True,
               name='mcpclient_pkg')

app = BUNDLE(coll,
             name='mcpclient.app',
             icon='assets/icon.icns',
             bundle_identifier='io.hatasaki.mcpclient',
             info_plist={
                 'CFBundleDisplayName': 'MCP Client for Microsoft Foundry',
                 'CFBundleName': 'MCP Client for Microsoft Foundry',
                 'CFBundleShortVersionString': SHORT_VERSION,
                 'CFBundleVersion': VERSION,
             })