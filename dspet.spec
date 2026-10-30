# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置：pyinstaller dspet.spec
from PyInstaller.utils.hooks import collect_all

datas = [('sprites', 'sprites'), ('lines.json', '.')]
binaries = []
hiddenimports = []
for mod in ('psutil', 'requests', 'pynvml'):
    tmp = collect_all(mod)
    datas += tmp[0]
    binaries += tmp[1]
    hiddenimports += tmp[2]

a = Analysis(
    ['dspet.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='dspet',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)
