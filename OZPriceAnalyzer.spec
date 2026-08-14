# -*- mode: python ; coding: utf-8 -*-

analysis = Analysis(
    ["scripts/desktop_entry.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=["bs4", "openpyxl", "pandas"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="OZPriceAnalyzer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
