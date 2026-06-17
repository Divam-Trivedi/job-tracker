#!/bin/bash
# build_mac.sh - Build Job Tracker for macOS
# NOTE: credentials.json is NOT bundled; users must supply their own

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Job Tracker"
APP_VERSION="1.0.0"
DMG_NAME="${APP_NAME}-${APP_VERSION}-mac.dmg"

echo "════════════════════════════════════════════════════════════"
echo "Building $APP_NAME for macOS"
echo "════════════════════════════════════════════════════════════"

# Step 1: Check dependencies
echo ""
echo "✓ Checking dependencies..."
echo "PATH=$PATH"
which create-dmg || echo "create-dmg not found in PATH"
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found. Install from https://www.python.org"
    exit 1
fi

if ! command -v create-dmg &> /dev/null; then
    echo "WARNING: create-dmg not found. Install with: brew install create-dmg"
    echo "Continuing without .dmg creation..."
    SKIP_DMG=1
else
    SKIP_DMG=0
fi

python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "  Python: $python_version"

# Step 2: Create virtual environment
echo ""
echo "✓ Setting up Python environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "  Created virtual environment"
else
    echo "  Virtual environment already exists"
fi

source venv/bin/activate

# Step 3: Install dependencies
echo ""
echo "✓ Installing Python dependencies..."
pip install --upgrade pip setuptools wheel > /dev/null
pip install \
    pyinstaller \
    flask \
    requests \
    keyring \
    google-auth-oauthlib \
    google-auth-httplib2 \
    google-api-python-client > /dev/null

# Optional dependencies for LLM providers
echo "  Installing optional LLM provider dependencies..."
pip install google-generativeai openai anthropic 2>/dev/null || true

# Step 4: Build with PyInstaller
echo ""
echo "✓ Building with PyInstaller..."
rm -rf build dist
pyinstaller build.spec --clean --noconfirm

if [ ! -d "dist/Job Tracker.app" ]; then
    echo "ERROR: Build failed"
    exit 1
fi

echo "  Created: dist/Job Tracker.app"

# Step 5: Create .dmg
if [ "$SKIP_DMG" -eq 0 ]; then
    echo ""
    echo "✓ Creating .dmg file..."
    rm -f "dist/$DMG_NAME" || true

    APP_PATH="dist/Job Tracker.app"
    APP_BUNDLE="Job Tracker.app"

    create-dmg \
    --volname "$APP_NAME" \
    --window-pos 200 120 \
    --window-size 600 400 \
    --icon-size 100 \
    --icon "Job Tracker.app" 150 190 \
    --hide-extension "Job Tracker.app" \
    --app-drop-link 450 190 \
    --no-internet-enable \
    "dist/$DMG_NAME" \
    "$APP_PATH"
    
    if [ -f "dist/$DMG_NAME" ]; then
        echo "  Created: dist/$DMG_NAME"
    else
        echo "  WARNING: .dmg creation failed, but .app is ready in dist/"
    fi
else
    echo "  Skipping .dmg (create-dmg not installed)"
fi

# Step 6: Create start script
echo ""
echo "✓ Creating launcher script..."
cat > "dist/Run Job Tracker.command" << 'EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
open "$SCRIPT_DIR/Job Tracker.app"
EOF
chmod +x "dist/Run Job Tracker.command"

# Step 7: Summary
echo ""
echo "════════════════════════════════════════════════════════════"
echo "✓ Build complete!"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Output:"
echo "  App:    dist/Job Tracker.app"
if [ "$SKIP_DMG" -eq 0 ] && [ -f "dist/$DMG_NAME" ]; then
    echo "  DMG:    dist/$DMG_NAME"
    echo ""
    echo "To distribute:"
    echo "  1. Share dist/$DMG_NAME"
    echo "  2. User downloads, double-clicks to mount"
    echo "  3. User drags 'Job Tracker.app' to Applications folder"
    echo "  4. User opens from Applications or Launchpad"
else
    echo "  DMG:    (not created; install create-dmg to enable)"
    echo ""
    echo "To distribute:"
    echo "  1. Compress: zip -r dist/Job\ Tracker-mac.zip dist/Job\ Tracker.app"
    echo "  2. Share the .zip file"
    echo "  3. User extracts and runs"
fi
echo ""
echo "First run setup:"
echo "  1. Open the app from Applications or run: open dist/Job\ Tracker.app"
echo "  2. App will create ~/Library/Application\\ Support/JobTracker/ automatically"
echo "  3. On first run, user clicks 'Allow' for Gmail OAuth"
echo "  4. User grants 'Read & Send' permissions"
echo "  5. token.json is saved securely (macOS Keychain via keyring library)"
echo "  6. Future runs: token loads from Keychain, no re-auth needed"
echo ""
echo "To distribute:"
echo "  1. Create DMG: hdiutil create -volname 'Job Tracker' -srcfolder dist/Job\\ Tracker.app -ov -format UDZO Job-Tracker.dmg"
echo "  2. Share the .dmg file"
echo "  3. Users double-click to mount, drag to Applications"
echo ""