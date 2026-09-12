"""WeightFlow application bootstrap."""
import secrets
from pathlib import Path

from flask import Flask

from admin import admin_bp
from communication_setting import communication_bp
from core.auth import register_auth
from core.database import database_connection, initialize_database
from logs import logs_bp
from main_screen import main_bp
from master_image import master_image_bp


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
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        SECRET_KEY=application_secret(application.instance_path),
    )
    application.config["PRODUCT_IMAGE_DIR"].mkdir(parents=True, exist_ok=True)
    initialize_database(application)
    application.register_blueprint(admin_bp)
    application.register_blueprint(main_bp)
    application.register_blueprint(communication_bp)
    application.register_blueprint(master_image_bp)
    application.register_blueprint(logs_bp)
    register_auth(application)
    return application


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
