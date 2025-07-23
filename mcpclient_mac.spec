# -*- mode: python ; coding: utf-8 -*-
"""macOS bundle build using version_info.txt."""
import sys, re
from pathlib import Path

_version_txt = Path('version_info.txt')
match = re.search(r"ProductVersion', '([\d\.]+)'", _version_txt.read_text('utf-8'))
VERSION = match.group(1) if match else '0.0.0'

a = Analysis(['app_runner.py'],
             pathex=[],
             binaries=[],
             datas=[('client/build', 'client/build')],
             hiddenimports=[],
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
                 'CFBundleDisplayName': 'MCP Client for Azure',
                 'CFBundleName': 'MCP Client for Azure',
                 'CFBundleShortVersionString': VERSION,
                 'CFBundleVersion': VERSION,
             })