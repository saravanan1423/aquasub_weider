from datetime import datetime
from flask import jsonify, redirect, render_template, request, session, url_for

from core.database import database_connection

SCREENS = {"main", "settings", "communication", "shifts", "melts", "allocation", "backup", "products", "logs"}
SETTINGS_SCREENS = {"communication", "shifts", "melts", "allocation", "backup", "products"}


def current_user_access():
    if not session.get("user_id"):
        return None, set()
    with database_connection() as connection:
        user = connection.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if user is None:
            return None, set()
        user = dict(user)
        user["default_screen"] = user.get("default_screen") or "main"
        user["keyboard_enabled"] = bool(user.get("keyboard_enabled", 1))
        permissions = {row["screen"] for row in connection.execute("SELECT screen FROM user_permissions WHERE user_id=?", (user["id"],))}
    return user, (SCREENS if user["role"] == "admin" else permissions)


def required_permissions():
    path, method = request.path, request.method
    if path == "/":
        user, permissions = current_user_access()
        if user and user.get("role") != "admin":
            if "main" not in permissions and "allocation" in permissions:
                return {"allocation"}
        return {"main"}
    if path == "/settings": return SETTINGS_SCREENS | {"settings"}
    if path in {"/contact-us", "/contactus", "/contact"}: return set()
    if path == "/communication": return {"communication"}
    if path == "/shifts": return {"shifts"}
    if path == "/melt-settings": return {"melts"}
    if path == "/material-allocation": return {"allocation"}
    if path.startswith("/api/material-allocations/"): return {"main", "allocation"} if method == "GET" else {"allocation"}
    if path == "/api/melt-threshold-settings": return {"admin"}
    if path == "/api/capture-display-settings": return {"admin"} if method == "PUT" else set()
    if path == "/images": return {"products"}
    if path == "/logs": return {"logs"}
    if path in {"/backup-settings", "/api/backup-settings"}: return {"backup"}
    if path in {"/backup-history", "/api/backup-history", "/api/automatic-backups"}: return {"logs", "backup"}
    if path in {"/api/usb-drives", "/api/report-usb-backup"}: return {"logs"}
    if path in {"/api/device-network", "/api/desktop/top-bar"}: return {"admin"}
    if path in {"/users", "/device-settings", "/api/device-settings"} or path.startswith("/api/users") or path in {"/api/change-password", "/api/rustdesk/open"}: return {"admin"}
    if path.endswith("/cancel") and path.startswith("/api/weight-captures/"): return {"main"}
    if path.startswith("/api/weight-captures/"): return {"admin"}
    if path == "/api/weight-captures": return {"main"} if method == "POST" else {"logs"}
    if path in {"/api/melts/complete", "/api/melts/unfinished"}: return {"main"}
    if path in {"/api/capture-report", "/api/capture-report-options"}: return {"logs"}
    if path == "/api/product-images": return {"main", "products", "melts", "allocation"} if method == "GET" else {"products"}
    if path.startswith("/api/product-images/"): return {"products"}
    if path == "/api/settings": return {"communication"} if method == "POST" else {"main", "communication"}
    if path == "/api/shifts" or path.startswith("/api/shifts/"): return {"shifts"}
    if path == "/api/current-shift": return set()
    if path == "/api/scale-status": return set()
    if path == "/api/melt-number-settings" or path.startswith("/api/melt-number-settings/"): return {"melts"}
    if path in {"/api/connect", "/api/data"}: return {"main", "communication"}
    if path in {"/api/ports", "/api/disconnect", "/api/clear"}: return {"communication"}
    return set()


def register_auth(app):
    @app.before_request
    def require_login():
        if request.endpoint in {"admin.login", "static", "logs.download_shared_report"}: return None
        shift_ends_at = session.get("shift_ends_at")
        if shift_ends_at and datetime.now().astimezone() >= datetime.fromisoformat(shift_ends_at):
            session.clear()
        user, permissions = current_user_access()
        if user is None:
            session.clear()
            if request.path.startswith("/api/"): return jsonify({"ok": False, "error": "Authentication required"}), 401
            return redirect(url_for("admin.login", next=request.full_path if request.query_string else request.path))
        required = required_permissions()
        if required and user["role"] != "admin" and not required.intersection(permissions):
            if request.path.startswith("/api/"): return jsonify({"ok": False, "error": "You do not have permission for this screen"}), 403
            return render_template("forbidden.html"), 403

    @app.context_processor
    def navigation_access():
        user, permissions = current_user_access()
        default_screen = user.get("default_screen", "main") if user else "main"
        keyboard_enabled = bool(user.get("keyboard_enabled", 1)) if user else True
        return {
            "current_username": user["username"] if user else "",
            "is_admin": bool(user and user["role"] == "admin"),
            "user_default_screen": default_screen,
            "keyboard_enabled": keyboard_enabled,
            "can_access_settings": bool(user and (user["role"] == "admin" or "settings" in permissions or SETTINGS_SCREENS.intersection(permissions))),
            "can_access": lambda screen: bool(user and (user["role"] == "admin" or screen in permissions)),
        }
