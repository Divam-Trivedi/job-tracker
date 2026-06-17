"""
launcher.py - Standalone desktop launcher for Job Tracker.

Starts Flask server in background thread, opens browser, shows system tray icon.
Works on Mac, Linux, and Windows.
"""

import os
import platform
import sys
import threading
import time
import webbrowser
from pathlib import Path

# Try to import Tkinter for system tray (available on all platforms)
try:
    import tkinter as tk
    from tkinter import messagebox
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False

# Flask will be bundled, so just import
try:
    from flask import Flask
except ImportError:
    print("ERROR: Flask not installed. Install with: pip install flask")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════

APP_NAME = "Job Tracker"
APP_VERSION = "1.0.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
STARTUP_TIMEOUT = 10  # seconds to wait for server to start


# ══════════════════════════════════════════════════════════════════════════════
# Determine base directory (where app files live)
# ══════════════════════════════════════════════════════════════════════════════

def get_app_root() -> Path:
    """
    Get the root directory where the app is installed.
    
    When bundled with PyInstaller:
      - sys.frozen is True
      - sys.executable points to the .app bundle (Mac) or .exe (Windows)
      - Real files are in _internal/ or alongside the executable
    
    When running from source:
      - __file__ points to launcher.py
    """
    if getattr(sys, 'frozen', False):
        # Bundled by PyInstaller
        if platform.system() == 'Darwin':
            # Mac: executable is in .app/Contents/MacOS/
            # App files are at .app/Contents/Resources/
            app_root = Path(sys.executable).parent.parent / "Resources"
        else:
            # Windows/Linux: files are next to the executable
            app_root = Path(sys.executable).parent
    else:
        # Running from source
        app_root = Path(__file__).parent
    
    return app_root.resolve()


# ══════════════════════════════════════════════════════════════════════════════
# Server startup
# ══════════════════════════════════════════════════════════════════════════════

class FlaskServerThread(threading.Thread):
    """Runs Flask server in background thread."""
    
    def __init__(self, app_root: Path, host: str, port: int):
        super().__init__(daemon=True)
        self.app_root = app_root
        self.host = host
        self.port = port
        self.ready = threading.Event()
        self.app = None
        
    def run(self):
        """Start the Flask server."""
        try:
            # Add app root to sys.path so imports work
            if str(self.app_root) not in sys.path:
                sys.path.insert(0, str(self.app_root))
            
            # Now import the Flask app (must happen after sys.path is set)
            from server import app
            self.app = app
            
            print(f"Starting Flask server on {self.host}:{self.port}")
            
            # Signal that we're ready (before .run() blocks)
            self.ready.set()
            
            # Run the server (blocking)
            self.app.run(
                host=self.host,
                port=self.port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )
        except Exception as e:
            print(f"Flask server error: {e}")
            self.ready.set()  # Signal ready even on error
    
    def wait_ready(self, timeout: int = STARTUP_TIMEOUT) -> bool:
        """Wait for server to be ready. Returns True if ready, False if timeout."""
        return self.ready.wait(timeout=timeout)


# ══════════════════════════════════════════════════════════════════════════════
# System tray (Mac/Windows)
# ══════════════════════════════════════════════════════════════════════════════

class TrayIcon:
    """Minimal system tray icon (Mac, Windows, Linux via Tkinter)."""
    
    def __init__(self, app_name: str, on_quit, on_open, url: str):
        self.app_name = app_name
        self.on_quit = on_quit
        self.on_open = on_open
        self.url = url
        self.root = None
        
    def show(self):
        """Show tray icon using Tkinter."""
        if not HAS_TKINTER:
            print("Tkinter not available; skipping system tray")
            return
        
        self.root = tk.Tk()
        self.root.withdraw()  # Hide the main window
        self.root.geometry("+0+0")
        self.root.title(self.app_name)
        
        # Create a context menu
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Open", command=self.on_open)
        menu.add_separator()
        menu.add_command(label="Quit", command=self.on_quit)
        
        # Mac: Use Cmd+Q to quit
        if platform.system() == 'Darwin':
            self.root.createcommand('tk::mac::Quit', self.on_quit)
        
        def show_menu(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        
        # Right-click to show menu
        self.root.bind("<Button-3>", show_menu)
        
        def on_closing():
            self.on_quit()
        
        self.root.protocol("WM_DELETE_WINDOW", on_closing)
        
        print("System tray icon ready")
        self.root.mainloop()
    
    def quit(self):
        """Destroy the tray icon."""
        if self.root:
            try:
                self.root.quit()
            except:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# Main launcher
# ══════════════════════════════════════════════════════════════════════════════

def find_free_port(host: str, start_port: int = DEFAULT_PORT) -> int:
    """Find a free port starting from start_port."""
    import socket
    for port in range(start_port, start_port + 100):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind((host, port))
            sock.close()
            return port
        except OSError:
            continue
    raise RuntimeError(f"No free ports found between {start_port} and {start_port + 100}")


def main():
    """Main entry point."""
    print("=" * 80)
    print(f"{APP_NAME} v{APP_VERSION} Launcher")
    print(f"Platform: {platform.system()}")
    print("=" * 80)
    
    # Get app root
    app_root = get_app_root()
    print(f"App root: {app_root}")

    # On first run, copy credentials.json from bundle to app data dir
    if getattr(sys, 'frozen', False):
        if platform.system() == 'Darwin':
            app_data = Path.home() / "Library" / "Application Support" / "JobTracker"
        elif platform.system() == 'Windows':
            app_data = Path(os.getenv('APPDATA', Path.home())) / "JobTracker"
        else:
            app_data = Path.home() / ".local" / "share" / "JobTracker"
        
        app_data.mkdir(parents=True, exist_ok=True)
        
        bundle_creds = app_root / "credentials.json"
        user_creds = app_data / "credentials.json"
        
        if bundle_creds.exists() and not user_creds.exists():
            import shutil
            shutil.copy(bundle_creds, user_creds)
            print(f"Copied credentials.json to {user_creds}")
    
    if not app_root.exists():
        error_msg = f"App root not found: {app_root}"
        print(error_msg)
        if HAS_TKINTER:
            messagebox.showerror("Error", error_msg)
        sys.exit(1)
    
    # Find a free port
    try:
        port = find_free_port(DEFAULT_HOST, DEFAULT_PORT)
        print(f"Using port {port}")
    except RuntimeError as e:
        print(str(e))
        if HAS_TKINTER:
            messagebox.showerror("Error", str(e))
        sys.exit(1)
    
    # Start Flask server in background
    server_thread = FlaskServerThread(app_root, DEFAULT_HOST, port)
    server_thread.start()
    
    # Wait for server to be ready
    print(f"Waiting for server to start (timeout: {STARTUP_TIMEOUT}s)...")
    if not server_thread.wait_ready(STARTUP_TIMEOUT):
        error_msg = f"Server did not start within {STARTUP_TIMEOUT} seconds"
        print(error_msg)
        if HAS_TKINTER:
            messagebox.showerror("Error", error_msg)
        sys.exit(1)
    
    # Open browser
    url = f"http://{DEFAULT_HOST}:{port}"
    print(f"Opening browser at {url}")
    time.sleep(0.5)  # Give Flask a moment to fully accept connections
    
    try:
        webbrowser.open(url)
    except Exception as e:
        print(f"Failed to open browser: {e}")
        print(f"\nManually open: {url}\n")
    
    # Show system tray icon
    if HAS_TKINTER:
        def on_quit():
            print("Quit via tray icon")
            sys.exit(0)
        
        def on_open():
            print("Opening browser from tray")
            try:
                webbrowser.open(url)
            except Exception as e:
                print(f"Failed to open browser: {e}")
        
        tray = TrayIcon(APP_NAME, on_quit, on_open, url)
        print("Showing system tray")
        tray.show()
    else:
        # No tray available; keep the process alive
        print(f"\n{APP_NAME} is running at {url}")
        print("Press Ctrl+C to quit.\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Interrupted by user")
            sys.exit(0)


if __name__ == "__main__":
    main()