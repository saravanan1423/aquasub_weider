import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory, session
from werkzeug.utils import secure_filename
from core.database import database_connection
from core.audit import record_changes

master_image_bp = Blueprint("master_image", __name__)
ALLOWED = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif", ".bmp", ".avif"}

def image_dir(): return current_app.config["PRODUCT_IMAGE_DIR"]
def master_dir(): return current_app.config["MASTER_IMAGE_DIR"]
def safe_name(name): return re.sub(r"_+", "_", secure_filename(name).strip("._-") or "image")[:80]
def furnace_folder(furnace):
    stored = Path(furnace["stored_filename"])
    if furnace["image_url"].startswith("/storage/master-images/"):
        return stored.parent
    return Path(f"{safe_name(furnace['image_name'])}_{furnace['id']}")
def asset_path(filename, url):
    if url.startswith("/storage/master-images/"):
        return master_dir() / filename
    return image_dir() / filename
def remove_unreferenced(filename, url):
    with database_connection() as connection:
        active = connection.execute("SELECT 1 FROM product_images WHERE stored_filename=? LIMIT 1", (filename,)).fetchone()
        captured = connection.execute("SELECT 1 FROM weight_captures WHERE image_url=? OR furnace_image_url=? LIMIT 1", (url, url)).fetchone()
    if not active and not captured:
        asset_path(filename, url).unlink(missing_ok=True)
def safe_upload(upload, name, folder):
    extension = Path(secure_filename(upload.filename)).suffix.lower()
    if extension not in ALLOWED or not (upload.mimetype or "").startswith("image/"): raise ValueError("Upload a valid JPG, PNG, WEBP, GIF, BMP, or AVIF image")
    now = datetime.now().astimezone()
    filename = f"{safe_name(name)}_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{extension}"
    relative = folder / filename
    target = master_dir() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    upload.save(target)
    return relative.as_posix(), f"/storage/master-images/{relative.as_posix()}", now

def migrate_legacy_images(app):
    with app.app_context(), database_connection() as connection:
        rows = connection.execute("SELECT * FROM product_images WHERE image_url LIKE '/storage/product-images/%' ORDER BY CASE WHEN image_type='furnace' THEN 0 ELSE 1 END, id").fetchall()
        for row in rows:
            source = image_dir() / row["stored_filename"]
            if not source.is_file():
                continue
            if row["image_type"] == "furnace":
                folder = Path(f"{safe_name(row['image_name'])}_{row['id']}")
            else:
                furnace = connection.execute("SELECT * FROM product_images WHERE id=? AND image_type='furnace'", (row["furnace_id"],)).fetchone()
                if furnace is None:
                    continue
                folder = furnace_folder(furnace) / "product_images"
            relative = folder / f"{safe_name(row['image_name'])}_{row['id']}{source.suffix}"
            target = master_dir() / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            connection.execute("UPDATE product_images SET stored_filename=?, image_url=? WHERE id=?", (relative.as_posix(), f"/storage/master-images/{relative.as_posix()}", row["id"]))

@master_image_bp.get("/images")
def page(): return render_template("image_master.html")
@master_image_bp.get("/storage/product-images/<path:filename>")
def image_file(filename): return send_from_directory(image_dir(), filename)
@master_image_bp.get("/storage/master-images/<path:filename>")
def master_image_file(filename): return send_from_directory(master_dir(), filename)
@master_image_bp.get("/api/product-images")
def images():
    with database_connection() as connection: rows=connection.execute("SELECT p.id,p.image_name,p.description,p.image_url,p.captured_at,p.created_by,p.created_at,p.image_type,p.furnace_id,f.image_name AS furnace_name FROM product_images p LEFT JOIN product_images f ON f.id=p.furnace_id ORDER BY CASE WHEN p.image_type='furnace' THEN 0 ELSE 1 END,p.id DESC").fetchall()
    return jsonify([dict(row) for row in rows])
@master_image_bp.post("/api/product-images")
def create():
    upload=request.files.get("image"); name=request.form.get("image_name","").strip(); description=request.form.get("description","").strip(); creator=session["username"]
    image_type=request.form.get("image_type","product").strip(); furnace_id=request.form.get("furnace_id","").strip()
    if not name or upload is None or not upload.filename: return jsonify({"ok":False,"error":"Image and image name are required"}),400
    if image_type not in {"furnace","product"}: return jsonify({"ok":False,"error":"Select a valid image type"}),400
    try: furnace_id=int(furnace_id) if image_type=="product" else None
    except ValueError: return jsonify({"ok":False,"error":"Select a parent furnace for this product"}),400
    with database_connection() as connection:
        if image_type=="furnace" and connection.execute("SELECT COUNT(*) FROM product_images WHERE image_type='furnace'").fetchone()[0]>=3: return jsonify({"ok":False,"error":"A maximum of 3 furnaces is allowed"}),400
        if image_type=="product":
            furnace=connection.execute("SELECT id,image_name,image_url,stored_filename FROM product_images WHERE id=? AND image_type='furnace'",(furnace_id,)).fetchone()
            if furnace is None: return jsonify({"ok":False,"error":"Select a valid furnace"}),400
            if connection.execute("SELECT COUNT(*) FROM product_images WHERE image_type='product' AND furnace_id=?",(furnace_id,)).fetchone()[0]>=6: return jsonify({"ok":False,"error":"A maximum of 6 images is allowed for each furnace"}),400
    folder = Path(f"{safe_name(name)}_{uuid.uuid4().hex[:8]}") if image_type=="furnace" else furnace_folder(furnace)/"product_images"
    try: filename,url,now=safe_upload(upload,name,folder)
    except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400
    try:
        with database_connection() as connection: image_id=connection.execute("INSERT INTO product_images (image_name,description,image_url,stored_filename,captured_at,created_by,created_at,image_type,furnace_id) VALUES (?,?,?,?,?,?,?,?,?)",(name,description or None,url,filename,now.isoformat(timespec="seconds"),creator,now.isoformat(timespec="seconds"),image_type,furnace_id)).lastrowid
    except sqlite3.Error: asset_path(filename,url).unlink(missing_ok=True); raise
    return jsonify({"ok":True,"id":image_id,"image_url":url}),201
@master_image_bp.put("/api/product-images/<int:image_id>")
def update(image_id):
    name=request.form.get("image_name","").strip(); description=request.form.get("description","").strip(); creator=session["username"]; upload=request.files.get("image")
    image_type=request.form.get("image_type","product").strip(); furnace_value=request.form.get("furnace_id","").strip()
    if not name: return jsonify({"ok":False,"error":"Image name is required"}),400
    with database_connection() as connection: old=connection.execute("SELECT * FROM product_images WHERE id=?",(image_id,)).fetchone()
    if old is None: return jsonify({"ok":False,"error":"Product image not found"}),404
    if image_type not in {"furnace","product"}: return jsonify({"ok":False,"error":"Select a valid image type"}),400
    try: furnace_id=int(furnace_value) if image_type=="product" else None
    except ValueError: return jsonify({"ok":False,"error":"Select a parent furnace for this product"}),400
    with database_connection() as connection:
        if image_type=="furnace" and old["image_type"]!="furnace" and connection.execute("SELECT COUNT(*) FROM product_images WHERE image_type='furnace'").fetchone()[0]>=3: return jsonify({"ok":False,"error":"A maximum of 3 furnaces is allowed"}),400
        if image_type=="product":
            if old["image_type"]=="furnace" and connection.execute("SELECT 1 FROM product_images WHERE furnace_id=? LIMIT 1",(image_id,)).fetchone(): return jsonify({"ok":False,"error":"Move this furnace's products before changing its type"}),400
            furnace=connection.execute("SELECT id,image_name,image_url,stored_filename FROM product_images WHERE id=? AND image_type='furnace'",(furnace_id,)).fetchone()
            if furnace is None or furnace_id==image_id: return jsonify({"ok":False,"error":"Select a valid furnace"}),400
            if connection.execute("SELECT COUNT(*) FROM product_images WHERE image_type='product' AND furnace_id=? AND id<>?",(furnace_id,image_id)).fetchone()[0]>=6: return jsonify({"ok":False,"error":"A maximum of 6 images is allowed for each furnace"}),400
    filename,url,new_file=old["stored_filename"],old["image_url"],False
    folder = (furnace_folder(old) if old["image_type"]=="furnace" else Path(f"{safe_name(name)}_{uuid.uuid4().hex[:8]}")) if image_type=="furnace" else furnace_folder(furnace)/"product_images"
    if upload and upload.filename:
        try: filename,url,_=safe_upload(upload,name,folder); new_file=True
        except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400
    elif old["image_type"]!=image_type or (image_type=="product" and old["furnace_id"]!=furnace_id):
        source=asset_path(filename,url)
        filename=f"{folder.as_posix()}/{source.stem}_{uuid.uuid4().hex[:8]}{source.suffix}"
        url=f"/storage/master-images/{filename}"
        target=asset_path(filename,url)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source,target)
        new_file=True
    try:
        with database_connection() as connection: connection.execute("UPDATE product_images SET image_name=?,description=?,created_by=?,image_url=?,stored_filename=?,image_type=?,furnace_id=? WHERE id=?",(name,description or None,creator,url,filename,image_type,furnace_id,image_id))
    except sqlite3.Error:
        if new_file: asset_path(filename,url).unlink(missing_ok=True)
        raise
    if new_file:
        remove_unreferenced(old["stored_filename"],old["image_url"])
    before={field:old[field] for field in ("image_name","description","image_url","stored_filename","created_by")}
    after={"image_name":name,"description":description or None,"image_url":url,"stored_filename":filename,"created_by":creator}
    record_changes("product_master",image_id,"edit",before,after)
    return jsonify({"ok":True,"id":image_id,"image_url":url})
@master_image_bp.delete("/api/product-images/<int:image_id>")
def delete(image_id):
    with database_connection() as connection:
        old=connection.execute("SELECT * FROM product_images WHERE id=?",(image_id,)).fetchone()
        if old is None: return jsonify({"ok":False,"error":"Product image not found"}),404
        if old["image_type"]=="furnace" and connection.execute("SELECT 1 FROM product_images WHERE furnace_id=? LIMIT 1",(image_id,)).fetchone(): return jsonify({"ok":False,"error":"Delete or move this furnace's products first"}),400
        connection.execute("DELETE FROM product_images WHERE id=?",(image_id,))
    remove_unreferenced(old["stored_filename"],old["image_url"])
    record_changes("product_master",image_id,"delete",{field:old[field] for field in ("image_name","description","image_url","stored_filename","created_by")},{})
    return jsonify({"ok":True})
