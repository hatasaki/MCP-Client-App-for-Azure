# -*- mode: python ; coding: utf-8 -*-
"""Enterprise Code Integrity-compatible Windows onedir build."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata
from scripts.version import current_version, format_version

_version_txt = Path('version_info.txt')
VERSION = format_version(current_version(_version_txt))

_packages = [
    'agent_framework',
    'agent_framework_foundry',
    'agent_framework_openai',
    'agent_framework_anthropic',
    'mcp',
    'openai',
    'anthropic',
    'azure.ai.projects',
    'azure.ai.inference',
    'azure.identity',
]
hiddenimports = [
    'backend.main',
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
    ('client\\build', 'client\\build'),
    ('assets\\loading.html', 'assets'),
    ('assets\\icon.ico', 'assets'),
]
for distribution in [
    'agent-framework-core',
    'agent-framework-openai',
    'agent-framework-foundry',
    'agent-framework-anthropic',
    'mcp',
    'openai',
    'anthropic',
    'azure-ai-projects',
    'azure-ai-inference',
    'azure-identity',
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

# Use the operating system's KnownDLL/API-set contracts. A Windows SDK or
# Performance Toolkit directory on PATH can otherwise inject a mismatched UCRT.
_windows_system_runtime_names = {
    name.lower()
    for name, _source, _typecode in a.binaries
    if name.lower() == 'ucrtbase.dll' or name.lower().startswith('api-ms-win-')
}
a.binaries = [
    entry for entry in a.binaries
    if entry[0].lower() not in _windows_system_runtime_names
]
pyz = PYZ(a.pure)

exe = EXE(pyz,
          a.scripts,
          exclude_binaries=True,
          name='mcpclient',
          console=False,
          version='version_info.txt',
          icon=['assets\\icon.ico'])

coll = COLLECT(exe,
               a.binaries,
               a.datas,
               strip=False,
               upx=True,
               name='mcpclient-onedir')
