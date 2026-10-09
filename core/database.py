import sqlite3
from datetime import datetime

from flask import current_app
from werkzeug.security import check_password_hash, generate_password_hash


def database_connection():
    path = current_app.config["DATABASE_PATH"]
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(app):
    with app.app_context(), database_connection() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS serial_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1), port TEXT NOT NULL,
                baud_rate INTEGER NOT NULL, data_bits INTEGER NOT NULL,
                parity TEXT NOT NULL, stop_bits TEXT NOT NULL,
                start_marker TEXT NOT NULL, end_marker TEXT NOT NULL,
                start_address INTEGER NOT NULL, end_address INTEGER NOT NULL,
                frame_timeout REAL NOT NULL DEFAULT 1.0,
                refresh_interval_ms INTEGER NOT NULL DEFAULT 200,
                frame_gap_ms INTEGER NOT NULL DEFAULT 250,
                created_by TEXT NOT NULL DEFAULT 'Unknown',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS product_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT, image_name TEXT NOT NULL,
                description TEXT, image_url TEXT NOT NULL,
                stored_filename TEXT NOT NULL UNIQUE, captured_at TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                image_type TEXT NOT NULL DEFAULT 'product',
                furnace_id INTEGER,
                melt_start_serial INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(furnace_id) REFERENCES product_images(id)
            );
            CREATE TABLE IF NOT EXISTS weight_captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_image_id INTEGER NOT NULL, image_name TEXT NOT NULL,
                image_url TEXT NOT NULL, weight TEXT NOT NULL,
                furnace_id TEXT, furnace_name TEXT, furnace_image_url TEXT,
                melt_number TEXT, melt_serial INTEGER, melt_completed_at TEXT,
                captured_at TEXT NOT NULL,
                captured_by_user_id INTEGER,
                captured_by_username TEXT NOT NULL DEFAULT 'Unknown',
                FOREIGN KEY(product_image_id) REFERENCES product_images(id)
            );
            CREATE INDEX IF NOT EXISTS idx_weight_captures_captured_at
                ON weight_captures(captured_at DESC);
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                show_in_login INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_permissions (
                user_id INTEGER NOT NULL, screen TEXT NOT NULL,
                PRIMARY KEY(user_id, screen),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module TEXT NOT NULL,
                record_id TEXT,
                action TEXT NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                changed_by_user_id INTEGER,
                changed_by_username TEXT NOT NULL,
                changed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_logs_changed_at
                ON audit_logs(changed_at DESC);
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                created_by TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS report_downloads (
                token TEXT PRIMARY KEY,
                report_json TEXT NOT NULL,
                download_url TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS device_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                device_id TEXT NOT NULL,
                location TEXT NOT NULL,
                installed_date TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS melt_threshold_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                individual_threshold_kg TEXT,
                melt_threshold_kg TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capture_display_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                success_display_seconds INTEGER NOT NULL DEFAULT 10,
                updated_at TEXT NOT NULL
            );
        """)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        if "role" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        if "show_in_login" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN show_in_login INTEGER NOT NULL DEFAULT 0")
        serial_columns = {row[1] for row in connection.execute("PRAGMA table_info(serial_settings)")}
        if "frame_timeout" not in serial_columns:
            connection.execute("ALTER TABLE serial_settings ADD COLUMN frame_timeout REAL NOT NULL DEFAULT 1.0")
        if "refresh_interval_ms" not in serial_columns:
            connection.execute("ALTER TABLE serial_settings ADD COLUMN refresh_interval_ms INTEGER NOT NULL DEFAULT 200")
        if "frame_gap_ms" not in serial_columns:
            connection.execute("ALTER TABLE serial_settings ADD COLUMN frame_gap_ms INTEGER NOT NULL DEFAULT 250")
        if "created_by" not in serial_columns:
            connection.execute("ALTER TABLE serial_settings ADD COLUMN created_by TEXT NOT NULL DEFAULT 'Unknown'")
        image_columns = {row[1] for row in connection.execute("PRAGMA table_info(product_images)")}
        if "image_type" not in image_columns:
            connection.execute("ALTER TABLE product_images ADD COLUMN image_type TEXT NOT NULL DEFAULT 'product'")
        if "furnace_id" not in image_columns:
            connection.execute("ALTER TABLE product_images ADD COLUMN furnace_id INTEGER")
        if "melt_start_serial" not in image_columns:
            connection.execute("ALTER TABLE product_images ADD COLUMN melt_start_serial INTEGER NOT NULL DEFAULT 1")
        capture_columns = {row[1] for row in connection.execute("PRAGMA table_info(weight_captures)")}
        if "captured_by_user_id" not in capture_columns:
            connection.execute("ALTER TABLE weight_captures ADD COLUMN captured_by_user_id INTEGER")
        if "captured_by_username" not in capture_columns:
            connection.execute("ALTER TABLE weight_captures ADD COLUMN captured_by_username TEXT NOT NULL DEFAULT 'Unknown'")
        if "furnace_id" not in capture_columns:
            connection.execute("ALTER TABLE weight_captures ADD COLUMN furnace_id TEXT")
        if "furnace_name" not in capture_columns:
            connection.execute("ALTER TABLE weight_captures ADD COLUMN furnace_name TEXT")
        if "furnace_image_url" not in capture_columns:
            connection.execute("ALTER TABLE weight_captures ADD COLUMN furnace_image_url TEXT")
        if "melt_number" not in capture_columns:
            connection.execute("ALTER TABLE weight_captures ADD COLUMN melt_number TEXT")
        if "melt_serial" not in capture_columns:
            connection.execute("ALTER TABLE weight_captures ADD COLUMN melt_serial INTEGER")
        if "melt_completed_at" not in capture_columns:
            connection.execute("ALTER TABLE weight_captures ADD COLUMN melt_completed_at TEXT")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_weight_captures_open_melt ON weight_captures(furnace_id,melt_number,melt_completed_at)")
        device_columns = {row[1] for row in connection.execute("PRAGMA table_info(device_settings)")}
        # Carry forward limits saved before they moved to Melt Number Settings.
        if {"individual_threshold_kg", "melt_threshold_kg"} <= device_columns:
            connection.execute("""
                INSERT OR IGNORE INTO melt_threshold_settings (
                    id, individual_threshold_kg, melt_threshold_kg, updated_at
                ) SELECT 1, individual_threshold_kg, melt_threshold_kg, updated_at
                  FROM device_settings WHERE id=1
            """)
        admin = connection.execute("SELECT id, password_hash FROM users WHERE username='admin'").fetchone()
        if admin is None:
            connection.execute(
                "INSERT INTO users (username,password_hash,role,show_in_login,created_at) VALUES (?,?,?,?,?)",
                ("admin", generate_password_hash("admin@123"), "admin", 0, datetime.now().astimezone().isoformat(timespec="seconds")),
            )
        else:
            connection.execute("UPDATE users SET role='admin',show_in_login=0 WHERE username='admin'")
            if check_password_hash(admin["password_hash"], "Admin@123"):
                connection.execute("UPDATE users SET password_hash=? WHERE username='admin'", (generate_password_hash("admin@123"),))
