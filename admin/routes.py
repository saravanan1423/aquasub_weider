import re
import sqlite3
from datetime import datetime
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from core.auth import SCREENS, current_user_access
from core.database import database_connection
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
            session.clear(); session.update(user_id=user["id"],username=user["username"],role=user["role"]); destination=request.args.get("next","")
            if not destination.startswith("/") or destination.startswith("//"):
                if user["role"]=="admin": destination="/"
                else:
                    _,permissions=current_user_access(); destination=next((path for screen,path in (("main","/"),("communication","/communication"),("products","/images"),("logs","/logs")) if screen in permissions),"/login")
            return redirect(destination)
        error="Invalid username or password"
    return render_template("login.html",error=error)
@admin_bp.post("/logout")
def logout(): session.clear(); return redirect(url_for("admin.login"))
@admin_bp.get("/users")
def page(): return render_template("user_management.html",screens=sorted(SCREENS))

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
            rows=connection.execute("SELECT id,username,role,created_at FROM users ORDER BY role,username").fetchall(); result=[]
            for user in rows:
                item=dict(user); item["permissions"]=[row["screen"] for row in connection.execute("SELECT screen FROM user_permissions WHERE user_id=? ORDER BY screen",(user["id"],))]; result.append(item)
        return jsonify(result)
    payload=request.get_json(silent=True) or {}; username=str(payload.get("username","")).strip(); password=str(payload.get("password",""))
    try:
        allowed=permissions(payload.get("permissions"))
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,50}",username): raise ValueError("Username must be 3–50 characters using letters, numbers, dot, dash, or underscore")
        if len(password)<6: raise ValueError("Password must contain at least 6 characters")
        with database_connection() as connection:
            user_id=connection.execute("INSERT INTO users (username,password_hash,role,created_at) VALUES (?,?,\'user\',?)",(username,generate_password_hash(password),datetime.now().astimezone().isoformat(timespec="seconds"))).lastrowid
            connection.executemany("INSERT INTO user_permissions (user_id,screen) VALUES (?,?)",[(user_id,screen) for screen in allowed])
    except sqlite3.IntegrityError: return jsonify({"ok":False,"error":"That username already exists"}),409
    except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400
    return jsonify({"ok":True,"id":user_id}),201
@admin_bp.route("/api/users/<int:user_id>",methods=["PUT","DELETE"])
def user(user_id):
    with database_connection() as connection:
        existing=connection.execute("SELECT id,username,role FROM users WHERE id=?",(user_id,)).fetchone()
        old_permissions=[row["screen"] for row in connection.execute("SELECT screen FROM user_permissions WHERE user_id=? ORDER BY screen",(user_id,))]
    if existing is None: return jsonify({"ok":False,"error":"User not found"}),404
    if existing["role"]=="admin": return jsonify({"ok":False,"error":"The administrator account cannot be changed or deleted"}),400
    if request.method=="DELETE":
        with database_connection() as connection: connection.execute("DELETE FROM user_permissions WHERE user_id=?",(user_id,)); connection.execute("DELETE FROM users WHERE id=?",(user_id,))
        return jsonify({"ok":True})
    payload=request.get_json(silent=True) or {}; username=str(payload.get("username","")).strip(); password=str(payload.get("password",""))
    try:
        allowed=permissions(payload.get("permissions"))
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,50}",username): raise ValueError("Enter a valid username")
        if password and len(password)<6: raise ValueError("Password must contain at least 6 characters")
        with database_connection() as connection:
            if password: connection.execute("UPDATE users SET username=?,password_hash=? WHERE id=?",(username,generate_password_hash(password),user_id))
            else: connection.execute("UPDATE users SET username=? WHERE id=?",(username,user_id))
            connection.execute("DELETE FROM user_permissions WHERE user_id=?",(user_id,)); connection.executemany("INSERT INTO user_permissions (user_id,screen) VALUES (?,?)",[(user_id,screen) for screen in allowed])
    except sqlite3.IntegrityError: return jsonify({"ok":False,"error":"That username already exists"}),409
    except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400
    before={"username":existing["username"],"permissions":old_permissions}
    after={"username":username,"permissions":sorted(allowed)}
    if password: before["password"]="protected"; after["password"]="changed"
    record_changes("users",user_id,"update",before,after)
    return jsonify({"ok":True})
