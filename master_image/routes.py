import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory, session
from werkzeug.utils import secure_filename
from core.database import database_connection
from core.audit import record_changes

master_image_bp = Blueprint("master_image", __name__)
ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

def image_dir(): return current_app.config["PRODUCT_IMAGE_DIR"]
def safe_upload(upload, name):
    extension = Path(secure_filename(upload.filename)).suffix.lower()
    if extension not in ALLOWED or not (upload.mimetype or "").startswith("image/"): raise ValueError("Upload a valid JPG, PNG, WEBP, or GIF image")
    now = datetime.now().astimezone(); safe_name = re.sub(r"_+", "_", secure_filename(name).strip("._-") or "image")[:80]
    filename = f"{safe_name}_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{extension}"
    upload.save(image_dir()/filename)
    return filename, f"/storage/product-images/{filename}", now

@master_image_bp.get("/images")
def page(): return render_template("image_master.html")
@master_image_bp.get("/storage/product-images/<path:filename>")
def image_file(filename): return send_from_directory(image_dir(), filename)
@master_image_bp.get("/api/product-images")
def images():
    with database_connection() as connection: rows=connection.execute("SELECT id,image_name,description,image_url,captured_at,created_by,created_at FROM product_images ORDER BY id DESC").fetchall()
    return jsonify([dict(row) for row in rows])
@master_image_bp.post("/api/product-images")
def create():
    upload=request.files.get("image"); name=request.form.get("image_name","").strip(); description=request.form.get("description","").strip(); creator=session["username"]
    if not name or upload is None or not upload.filename: return jsonify({"ok":False,"error":"Image and image name are required"}),400
    with database_connection() as connection: count=connection.execute("SELECT COUNT(*) FROM product_images").fetchone()[0]
    if count>=8: return jsonify({"ok":False,"error":"A maximum of 8 product images is allowed"}),400
    try: filename,url,now=safe_upload(upload,name)
    except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400
    try:
        with database_connection() as connection: image_id=connection.execute("INSERT INTO product_images (image_name,description,image_url,stored_filename,captured_at,created_by,created_at) VALUES (?,?,?,?,?,?,?)",(name,description or None,url,filename,now.isoformat(timespec="seconds"),creator,now.isoformat(timespec="seconds"))).lastrowid
    except sqlite3.Error: (image_dir()/filename).unlink(missing_ok=True); raise
    return jsonify({"ok":True,"id":image_id,"image_url":url}),201
@master_image_bp.put("/api/product-images/<int:image_id>")
def update(image_id):
    name=request.form.get("image_name","").strip(); description=request.form.get("description","").strip(); creator=session["username"]; upload=request.files.get("image")
    if not name: return jsonify({"ok":False,"error":"Image name is required"}),400
    with database_connection() as connection: old=connection.execute("SELECT * FROM product_images WHERE id=?",(image_id,)).fetchone()
    if old is None: return jsonify({"ok":False,"error":"Product image not found"}),404
    filename,url,new_file=old["stored_filename"],old["image_url"],False
    if upload and upload.filename:
        try: filename,url,_=safe_upload(upload,name); new_file=True
        except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400
    try:
        with database_connection() as connection: connection.execute("UPDATE product_images SET image_name=?,description=?,created_by=?,image_url=?,stored_filename=? WHERE id=?",(name,description or None,creator,url,filename,image_id))
    except sqlite3.Error:
        if new_file: (image_dir()/filename).unlink(missing_ok=True)
        raise
    if new_file:
        with database_connection() as connection: captured=connection.execute("SELECT 1 FROM weight_captures WHERE product_image_id=? LIMIT 1",(image_id,)).fetchone()
        if not captured: (image_dir()/old["stored_filename"]).unlink(missing_ok=True)
    before={field:old[field] for field in ("image_name","description","image_url","stored_filename","created_by")}
    after={"image_name":name,"description":description or None,"image_url":url,"stored_filename":filename,"created_by":creator}
    record_changes("product_master",image_id,"edit",before,after)
    return jsonify({"ok":True,"id":image_id,"image_url":url})
@master_image_bp.delete("/api/product-images/<int:image_id>")
def delete(image_id):
    with database_connection() as connection:
        old=connection.execute("SELECT * FROM product_images WHERE id=?",(image_id,)).fetchone()
        if old is None: return jsonify({"ok":False,"error":"Product image not found"}),404
        captured=connection.execute("SELECT 1 FROM weight_captures WHERE product_image_id=? LIMIT 1",(image_id,)).fetchone(); connection.execute("DELETE FROM product_images WHERE id=?",(image_id,))
    if not captured: (image_dir()/old["stored_filename"]).unlink(missing_ok=True)
    record_changes("product_master",image_id,"delete",{field:old[field] for field in ("image_name","description","image_url","stored_filename","created_by")},{})
    return jsonify({"ok":True})
