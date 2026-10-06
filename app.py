"""WeightFlow application bootstrap."""
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask

from admin import admin_bp
from communication_setting import communication_bp
from core.auth import register_auth
from core.database import database_connection, initialize_database
from logs import logs_bp
from main_screen import main_bp
from master_image import master_image_bp
from master_image.routes import migrate_legacy_images


def application_secret(instance_path):
    secret_file = Path(instance_path) / ".session_secret"
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    if not secret_file.exists():
        secret_file.write_text(secrets.token_hex(32), encoding="utf-8")
        secret_file.chmod(0o600)
    return secret_file.read_text(encoding="utf-8").strip()


def create_app():
    application = Flask(__name__)
    application.config.update(
        DATABASE_PATH=Path(application.instance_path) / "serial_monitor.db",
        PRODUCT_IMAGE_DIR=Path(application.root_path) / "storage" / "product_images",
        MASTER_IMAGE_DIR=Path(application.root_path) / "storage" / "master_images",
        MAX_CONTENT_LENGTH=25 * 1024 * 1024,
        SECRET_KEY=application_secret(application.instance_path),
    )
    application.config["PRODUCT_IMAGE_DIR"].mkdir(parents=True, exist_ok=True)
    application.config["MASTER_IMAGE_DIR"].mkdir(parents=True, exist_ok=True)
    initialize_database(application)
    migrate_legacy_images(application)
    application.register_blueprint(admin_bp)
    application.register_blueprint(main_bp)
    application.register_blueprint(communication_bp)
    application.register_blueprint(master_image_bp)
    application.register_blueprint(logs_bp)
    register_auth(application)

    @application.after_request
    def prevent_stale_ui(response):
        if response.mimetype in {"text/html", "text/css", "application/javascript", "text/javascript"}:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    return application


app = create_app()


def find_browser():
    browser_names = (
        "msedge",
        "chrome",
        "google-chrome",
        "chromium",
        "chromium-browser",
        "firefox",
    )
    for name in browser_names:
        executable = shutil.which(name)
        if executable:
            return executable

    locations = (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    )
    return next((str(location) for location in locations if location.is_file()), None)


def open_fullscreen_browser(url, host, port):
    for _ in range(100):
        try:
            with socket.create_connection((host, port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)
    else:
        return

    browser = find_browser()
    if browser:
        name = Path(browser).name.lower()
        if "firefox" in name:
            command = [browser, "--kiosk", url]
        elif "edge" in name:
            command = [browser, "--start-fullscreen", f"--app={url}"]
        else:
            profile = Path(app.instance_path) / "chromium-kiosk-profile"
            profile.mkdir(parents=True, exist_ok=True)
            command = [
                browser,
                f"--user-data-dir={profile}",
                "--kiosk",
                "--incognito",
                "--no-first-run",
                "--noerrdialogs",
                "--disable-infobars",
                "--disable-session-crashed-bubble",
                url,
            ]
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            pass

    webbrowser.open(url, new=1)


if __name__ == "__main__":
    host = "127.0.0.1"
    port = 5000
    url = f"http://{host}:{port}"
    if os.environ.get("WEIGHTFLOW_AUTO_FULLSCREEN", "1").lower() not in {"0", "false", "no"}:
        threading.Thread(
            target=open_fullscreen_browser,
            args=(url, host, port),
            daemon=True,
        ).start()
    app.run(host="0.0.0.0", port=port, debug=True, use_reloader=False)
