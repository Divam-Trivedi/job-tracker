# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Job Tracker.

Usage:
  Mac:   pyinstaller build.spec --target-arch=arm64  # or x86_64
  Windows: pyinstaller build.spec
  
Output:
  Mac:   dist/Job\ Tracker.app
  Windows: dist/Job Tracker.exe
"""

import sys
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
# Analysis
# ══════════════════════════════════════════════════════════════════════════════

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('credentials.json', '.'),  # Bundle credentials.json
        ('index.html', '.'),
        ('server.py', '.'),
        ('main.py', '.'),
        ('gmail_client.py', '.'),
        ('ollama_classifier.py', '.'),
        ('database.py', '.'),
        ('api_key_storage.py', '.'),
        ('config.py', '.'),
        # DO NOT include token.json - it's created at runtime
    ],
    hiddenimports=[
        'flask',
        'google.auth.oauthlib.flow',
        'google.auth.transport.requests',
        'google.api_core',
        'google.api_core.gapic_v1',
        'google.auth',
        'google.auth.crypt',
        'google.auth.jwt',
        'requests',
        'google.generativeai',  # For Gemini support (optional)
        'openai',               # For OpenAI support (optional)
        'anthropic',            # For Anthropic support (optional)
        'cryptography',         # For encrypted API key storage
        'cryptography.fernet',  # Fernet encryption
        'keyring',              # OS keyring support
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludedimports=[],
    noarchive=False,
)

# ══════════════════════════════════════════════════════════════════════════════
# PYZ (Python archive)
# ══════════════════════════════════════════════════════════════════════════════

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

# ══════════════════════════════════════════════════════════════════════════════
# EXE / APP
# ══════════════════════════════════════════════════════════════════════════════

if sys.platform == 'darwin':
    # macOS: Create .app bundle
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='Job Tracker',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,  # Use system default or pass via CLI
        codesign_identity=None,
        entitlements_file=None,
    )
    
    app = BUNDLE(
        exe,
        name='Job Tracker.app',
        icon="JobTracker.icns",
        bundle_identifier='com.jobtracker.app',
        info_plist={
            'NSPrincipalClass': 'NSApplication',
            'NSHighResolutionCapable': 'True',
        },
    )
else:
    # Windows / Linux: Create executable
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name='Job Tracker',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,  # Don't show console window
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
