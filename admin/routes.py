import re
import sqlite3
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from core.auth import SCREENS, current_user_access
from core.database import database_connection
from core.shifts import active_shift
from core.audit import record_changes

admin_bp=Blueprint("admin",__name__)

@admin_bp.route("/login",methods=["GET","POST"])
def login():
    if session.get("user_id"): return redirect(url_for("main_screen.index"))
    error=""
    if request.method=="POST":
        username=request.form.get("username","").strip(); password=request.form.get("password","")
        with database_connection() as connection: user=connection.execute("SELECT id,username,password_hash,role FROM users WHERE username=?",(username,)).fetchone()
        if user and check_password_hash(user["password_hash"],password):
            session.clear(); session.update(user_id=user["id"],username=user["username"],role=user["role"])
            with database_connection() as connection:
                shift = active_shift(connection)
            if shift["active"]: session["shift_ends_at"] = shift["ends_at"]
            destination=request.args.get("next","")
            if not destination.startswith("/") or destination.startswith("//"):
                if user["role"]=="admin": destination="/"
                else:
                    _,permissions=current_user_access(); destination=next((path for screen,path in (("main","/"),("communication","/communication"),("products","/images"),("logs","/logs"),("shifts","/shifts"),("melts","/melt-settings")) if screen in permissions),"/login")
            return redirect(destination)
        error="Invalid username or password"
    with database_connection() as connection:
        usernames = [row["username"] for row in connection.execute(
            "SELECT username FROM users WHERE role!='admin' AND show_in_login=1 ORDER BY username COLLATE NOCASE"
        )]
    return render_template("login.html", error=error, usernames=usernames,
                           selected_username=request.form.get("username", ""))
@admin_bp.post("/logout")
def logout(): session.clear(); return redirect(url_for("admin.login"))
@admin_bp.get("/users")
def page(): return render_template("user_management.html",screens=sorted(SCREENS))


def raspberry_pi_serial():
    for path in ("/proc/device-tree/serial-number", "/sys/firmware/devicetree/base/serial-number"):
        try:
            serial = Path(path).read_bytes().rstrip(b"\x00").decode("ascii").strip()
        except (OSError, UnicodeError):
            continue
        if re.fullmatch(r"[0-9a-fA-F]{8,16}", serial):
            return serial
    try:
        cpuinfo = Path("/proc/cpuinfo").read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None
    match = re.search(r"^Serial\s*:\s*([0-9a-fA-F]{8,16})\s*$", cpuinfo, re.MULTILINE)
    return match.group(1) if match else None


@admin_bp.get("/device-settings")
def device_settings_page():
    return render_template("device_settings.html")


@admin_bp.route("/api/device-settings", methods=["GET", "PUT"])
def device_settings():
    with database_connection() as connection:
        row = connection.execute(
            "SELECT device_id, location, installed_date FROM device_settings WHERE id=1"
        ).fetchone()
    if request.method == "GET":
        return jsonify({"pi_serial": raspberry_pi_serial(), **(dict(row) if row else {
            "device_id": "", "location": "", "installed_date": ""
        })})

    payload = request.get_json(silent=True) or {}
    device_id = str(payload.get("device_id", "")).strip()
    location = str(payload.get("location", "")).strip()
    installed_date = str(payload.get("installed_date", "")).strip()
    if not re.fullmatch(r"[0-9]{5}", device_id):
        return jsonify({"error": "Device ID must be exactly 5 digits"}), 400
    if not location or len(location) > 120:
        return jsonify({"error": "Location must be between 1 and 120 characters"}), 400
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", installed_date):
            raise ValueError
        datetime.strptime(installed_date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Enter a valid installation date"}), 400
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with database_connection() as connection:
        connection.execute("""
            INSERT INTO device_settings (id, device_id, location, installed_date, updated_at)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET device_id=excluded.device_id,
                location=excluded.location, installed_date=excluded.installed_date,
                updated_at=excluded.updated_at
        """, (device_id, location, installed_date, updated_at))
    before = dict(row) if row else {}
    record_changes("device_settings", 1, "update", before, {
        "device_id": device_id, "location": location, "installed_date": installed_date
    })
    return jsonify({"ok": True, "device_id": device_id, "location": location,
                    "installed_date": installed_date, "pi_serial": raspberry_pi_serial()})

@admin_bp.post("/api/rustdesk/open")
def open_rustdesk():
    if not sys.platform.startswith("linux"):
        return jsonify({"ok": False, "error": "RustDesk can only be opened on the Raspberry Pi"}), 503
    environment = os.environ.copy()
    runtime_dir = Path(environment.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    if runtime_dir.is_dir():
        environment["XDG_RUNTIME_DIR"] = str(runtime_dir)
    if not environment.get("DISPLAY") and not environment.get("WAYLAND_DISPLAY"):
        wayland_socket = next(
            (path for path in sorted(runtime_dir.glob("wayland-*")) if path.is_socket()),
            None,
        )
        if wayland_socket:
            environment["WAYLAND_DISPLAY"] = wayland_socket.name
        elif Path("/tmp/.X11-unix/X0").exists():
            environment["DISPLAY"] = ":0"
        else:
            return jsonify({"ok": False, "error": "No desktop session is available on this device"}), 503
    executable = shutil.which("rustdesk")
    if executable:
        command = [executable]
    else:
        flatpak = shutil.which("flatpak")
        if not flatpak:
            return jsonify({"ok": False, "error": "RustDesk is not installed on this device"}), 503
        try:
            installed = subprocess.run(
                [flatpak, "info", "com.rustdesk.RustDesk"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            ).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            installed = False
        if not installed:
            return jsonify({"ok": False, "error": "RustDesk is not installed on this device"}), 503
        command = [flatpak, "run", "com.rustdesk.RustDesk"]
    try:
        subprocess.Popen(
            command, env=environment, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        return jsonify({"ok": False, "error": "RustDesk could not be opened on this device"}), 503
    return jsonify({"ok": True, "message": "RustDesk is opening on this device"})

@admin_bp.post("/api/change-password")
def change_password():
    payload=request.get_json(silent=True) or {}; current=str(payload.get("current_password","")); new=str(payload.get("new_password","")); confirmation=str(payload.get("confirm_password",""))
    if len(new)<8: return jsonify({"ok":False,"error":"New password must contain at least 8 characters"}),400
    if new!=confirmation: return jsonify({"ok":False,"error":"New password and confirmation do not match"}),400
    if current==new: return jsonify({"ok":False,"error":"New password must be different from the current password"}),400
    with database_connection() as connection:
        user=connection.execute("SELECT password_hash FROM users WHERE id=?",(session["user_id"],)).fetchone()
        if user is None or not check_password_hash(user["password_hash"],current): return jsonify({"ok":False,"error":"Current password is incorrect"}),400
        connection.execute("UPDATE users SET password_hash=? WHERE id=?",(generate_password_hash(new),session["user_id"]))
    record_changes("users",session["user_id"],"change_password",{"password":"protected"},{"password":"changed"})
    return jsonify({"ok":True,"message":"Password changed successfully"})

def permissions(values):
    result={str(value) for value in (values or [])}
    if not result<=SCREENS: raise ValueError("Invalid screen permission")
    return result
@admin_bp.route("/api/users",methods=["GET","POST"])
def users():
    if request.method=="GET":
        with database_connection() as connection:
            rows=connection.execute("SELECT id,username,role,show_in_login,created_at FROM users ORDER BY role,username").fetchall(); result=[]
            for user in rows:
                item=dict(user); item["show_in_login"]=bool(item["show_in_login"]); item["permissions"]=[row["screen"] for row in connection.execute("SELECT screen FROM user_permissions WHERE user_id=? ORDER BY screen",(user["id"],))]; result.append(item)
        return jsonify(result)
    payload=request.get_json(silent=True) or {}; username=str(payload.get("username","")).strip(); password=str(payload.get("password",""))
    try:
        allowed=permissions(payload.get("permissions"))
        show_in_login=payload.get("show_in_login",False)
        if not isinstance(show_in_login,bool): raise ValueError("Choose whether to show this user at login")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,50}",username): raise ValueError("Username must be 3–50 characters using letters, numbers, dot, dash, or underscore")
        if len(password)<6: raise ValueError("Password must contain at least 6 characters")
        with database_connection() as connection:
            user_id=connection.execute("INSERT INTO users (username,password_hash,role,show_in_login,created_at) VALUES (?,?,\'user\',?,?)",(username,generate_password_hash(password),int(show_in_login),datetime.now().astimezone().isoformat(timespec="seconds"))).lastrowid
            connection.executemany("INSERT INTO user_permissions (user_id,screen) VALUES (?,?)",[(user_id,screen) for screen in allowed])
    except sqlite3.IntegrityError: return jsonify({"ok":False,"error":"That username already exists"}),409
    except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400
    return jsonify({"ok":True,"id":user_id}),201
@admin_bp.route("/api/users/<int:user_id>",methods=["PUT","DELETE"])
def user(user_id):
    with database_connection() as connection:
        existing=connection.execute("SELECT id,username,role,show_in_login FROM users WHERE id=?",(user_id,)).fetchone()
        old_permissions=[row["screen"] for row in connection.execute("SELECT screen FROM user_permissions WHERE user_id=? ORDER BY screen",(user_id,))]
    if existing is None: return jsonify({"ok":False,"error":"User not found"}),404
    if existing["role"]=="admin": return jsonify({"ok":False,"error":"The administrator account cannot be changed or deleted"}),400
    if request.method=="DELETE":
        with database_connection() as connection: connection.execute("DELETE FROM user_permissions WHERE user_id=?",(user_id,)); connection.execute("DELETE FROM users WHERE id=?",(user_id,))
        return jsonify({"ok":True})
    payload=request.get_json(silent=True) or {}; username=str(payload.get("username","")).strip(); password=str(payload.get("password",""))
    try:
        allowed=permissions(payload.get("permissions"))
        show_in_login=payload.get("show_in_login",bool(existing["show_in_login"]))
        if not isinstance(show_in_login,bool): raise ValueError("Choose whether to show this user at login")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,50}",username): raise ValueError("Enter a valid username")
        if password and len(password)<6: raise ValueError("Password must contain at least 6 characters")
        with database_connection() as connection:
            if password: connection.execute("UPDATE users SET username=?,password_hash=?,show_in_login=? WHERE id=?",(username,generate_password_hash(password),int(show_in_login),user_id))
            else: connection.execute("UPDATE users SET username=?,show_in_login=? WHERE id=?",(username,int(show_in_login),user_id))
            connection.execute("DELETE FROM user_permissions WHERE user_id=?",(user_id,)); connection.executemany("INSERT INTO user_permissions (user_id,screen) VALUES (?,?)",[(user_id,screen) for screen in allowed])
    except sqlite3.IntegrityError: return jsonify({"ok":False,"error":"That username already exists"}),409
    except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400
    before={"username":existing["username"],"permissions":old_permissions,"show_in_login":bool(existing["show_in_login"])}
    after={"username":username,"permissions":sorted(allowed),"show_in_login":show_in_login}
    if password: before["password"]="protected"; after["password"]="changed"
    record_changes("users",user_id,"update",before,after)
    return jsonify({"ok":True})
