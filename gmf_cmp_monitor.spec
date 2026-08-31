# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone GMF CMP Monitor portable build."""

import os

from PyInstaller.building.build_main import Analysis, COLLECT, EXE, PYZ
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

PROJECT_ROOT = os.path.abspath(SPECPATH)

block_cipher = None

hidden_imports = [
    "playwright",
    "playwright.sync_api",
    "tzdata",
    "dotenv",
    "python_dotenv",
]
hidden_imports += collect_submodules("playwright")

datas = collect_data_files("playwright")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_playwright", "pandas", "numpy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

options = []

exe = EXE(
    pyz,
    a.scripts,
    options,
    exclude_binaries=True,
    name="GMF-CMP-Monitor",
    icon=os.path.join(PROJECT_ROOT, "assets", "app.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="GMF-CMP-Monitor",
)