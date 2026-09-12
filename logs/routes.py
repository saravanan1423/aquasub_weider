from datetime import datetime
from flask import Blueprint, jsonify, render_template, request
from core.database import database_connection
from core.audit import record_changes

logs_bp = Blueprint("logs", __name__)

@logs_bp.get("/logs")
def page(): return render_template("capture_logs.html")

@logs_bp.get("/api/weight-captures")
def captures():
    if "page" in request.args:
        try: page=max(1,int(request.args.get("page",1))); per_page=max(1,min(int(request.args.get("per_page",5)),50))
        except ValueError: page,per_page=1,5
        with database_connection() as connection:
            total=connection.execute("SELECT COUNT(*) FROM weight_captures").fetchone()[0]; pages=max(1,(total+per_page-1)//per_page); page=min(page,pages)
            rows=connection.execute("SELECT id,product_image_id,image_name,image_url,weight,captured_at,captured_by_username FROM weight_captures ORDER BY id DESC LIMIT ? OFFSET ?",(per_page,(page-1)*per_page)).fetchall()
        return jsonify({"items":[dict(row) for row in rows],"total":total,"page":page,"per_page":per_page,"pages":pages})
    try: limit=max(1,min(int(request.args.get("limit",20)),200))
    except ValueError: limit=20
    with database_connection() as connection: rows=connection.execute("SELECT id,product_image_id,image_name,image_url,weight,captured_at,captured_by_username FROM weight_captures ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
    return jsonify([dict(row) for row in rows])

@logs_bp.route("/api/weight-captures/<int:capture_id>",methods=["PUT","DELETE"])
def capture(capture_id):
    with database_connection() as connection: existing=connection.execute("SELECT * FROM weight_captures WHERE id=?",(capture_id,)).fetchone()
    if existing is None: return jsonify({"ok":False,"error":"Capture log not found"}),404
    if request.method=="DELETE":
        with database_connection() as connection: connection.execute("DELETE FROM weight_captures WHERE id=?",(capture_id,))
        record_changes("capture_logs",capture_id,"delete",{field:existing[field] for field in ("image_name","weight","captured_at","captured_by_username")},{})
        return jsonify({"ok":True})
    payload=request.get_json(silent=True) or {}; name=str(payload.get("image_name","")).strip(); weight=str(payload.get("weight","")).strip(); captured_at=str(payload.get("captured_at","")).strip()
    if not name or not weight or not captured_at: return jsonify({"ok":False,"error":"Product name, weight, and capture time are required"}),400
    try: datetime.fromisoformat(captured_at.replace("Z","+00:00"))
    except ValueError: return jsonify({"ok":False,"error":"Enter a valid capture date and time"}),400
    with database_connection() as connection: connection.execute("UPDATE weight_captures SET image_name=?,weight=?,captured_at=? WHERE id=?",(name,weight,captured_at,capture_id))
    record_changes("capture_logs",capture_id,"edit",{"image_name":existing["image_name"],"weight":existing["weight"],"captured_at":existing["captured_at"]},{"image_name":name,"weight":weight,"captured_at":captured_at})
    return jsonify({"ok":True})
