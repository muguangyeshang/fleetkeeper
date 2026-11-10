# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['scripts/exe_entry.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=['fleetkeeper.ui', 'fleetkeeper.cli', 'fleetkeeper.watchdog',
                   'fleetkeeper.page', 'fleetkeeper.manage',
                   'fleetkeeper.discover', 'fleetkeeper.quota',
                   'fleetkeeper.quota_page'],
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
    a.binaries,
    a.datas,
    [],
    name='fleetkeeper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
