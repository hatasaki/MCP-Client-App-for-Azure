# -*- mode: python ; coding: utf-8 -*-
"""Windows one-file build using version_info.txt."""
import re
from pathlib import Path

_version_txt = Path('version_info.txt')
match = re.search(r"ProductVersion', '([\d\.]+)'", _version_txt.read_text('utf-8'))
VERSION = match.group(1) if match else '0.0.0'

a = Analysis(['app_runner.py'],
             pathex=[],
             binaries=[],
             datas=[('client\\build', 'client\\build')],
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
          a.binaries,
          a.datas,
          [],
          name='mcpclient',
          onefile=True,
          console=False,
          version='version_info.txt',
          icon=['assets\\icon.ico'])
