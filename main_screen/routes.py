from datetime import datetime
from flask import Blueprint, jsonify, render_template, request, session
from core.database import database_connection

main_bp = Blueprint("main_screen", __name__)

@main_bp.get("/")
def index(): return render_template("main.html")

@main_bp.post("/api/weight-captures")
def create_weight_capture():
    payload = request.get_json(silent=True) or {}; weight = str(payload.get("weight", "")).strip()
    try: image_id = int(payload.get("product_image_id"))
    except (TypeError, ValueError): return jsonify({"ok": False, "error": "Select a valid product image"}), 400
    if not weight or weight == "------": return jsonify({"ok": False, "error": "Wait for a live weight before selecting an image"}), 400
    with database_connection() as connection:
        image = connection.execute("SELECT id,image_name,image_url FROM product_images WHERE id=?", (image_id,)).fetchone()
        if image is None: return jsonify({"ok": False, "error": "The selected product image no longer exists"}), 404
        captured_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        cursor = connection.execute("INSERT INTO weight_captures (product_image_id,image_name,image_url,weight,captured_at,captured_by_user_id,captured_by_username) VALUES (?,?,?,?,?,?,?)", (image["id"],image["image_name"],image["image_url"],weight,captured_at,session["user_id"],session["username"]))
    return jsonify({"ok": True,"id":cursor.lastrowid,"image_name":image["image_name"],"image_url":image["image_url"],"weight":weight,"captured_at":captured_at}), 201

@main_bp.post("/api/weight-captures/<int:capture_id>/cancel")
def cancel_weight_capture(capture_id):
    with database_connection() as connection:
        capture = connection.execute("SELECT id,captured_by_user_id,captured_at FROM weight_captures WHERE id=?", (capture_id,)).fetchone()
        if capture is None: return jsonify({"ok":False,"error":"Capture not found or already cancelled"}),404
        if capture["captured_by_user_id"] != session["user_id"]: return jsonify({"ok":False,"error":"You can only cancel your own capture"}),403
        captured_time=datetime.fromisoformat(capture["captured_at"])
        if (datetime.now().astimezone()-captured_time).total_seconds()>15: return jsonify({"ok":False,"error":"The cancellation period has expired"}),400
        connection.execute("DELETE FROM weight_captures WHERE id=?",(capture_id,))
    return jsonify({"ok":True})
