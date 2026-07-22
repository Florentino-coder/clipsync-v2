# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

_spec_dir = Path(SPECPATH)
_datas = [('assets\\clipsync_icon.png', 'assets')]
if (_spec_dir / 'chrome-extension' / 'manifest.json').is_file():
    _datas.append(('chrome-extension', 'chrome-extension'))

a = Analysis(
    ['clipsync_pc.py'],
    pathex=[],
    binaries=[],
    datas=_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ClipSyncPC',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\clipsync.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ClipSyncPC',
)
