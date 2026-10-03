import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, jsonify, render_template, request, session
from core.database import database_connection
from core.audit import record_changes

main_bp = Blueprint("main_screen", __name__)

@main_bp.get("/")
def index(): return render_template("main.html")

@main_bp.get("/melt-settings")
def melt_settings_page(): return render_template("melt_number_settings.html")

@main_bp.get("/api/furnaces")
def furnaces():
    with database_connection() as connection:
        rows=connection.execute("SELECT id,image_name AS name,image_url FROM product_images WHERE image_type='furnace' ORDER BY id").fetchall()
    return jsonify([dict(row) for row in rows])


def next_melt_serial(connection, furnace_id, start_serial):
    used = connection.execute(
        "SELECT COALESCE(MAX(melt_serial),0) FROM weight_captures WHERE furnace_id=?",
        (str(furnace_id),),
    ).fetchone()[0]
    return max(start_serial, int(used) + 1), int(used)

def numeric_weight(value):
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else 0.0


def weight_amount(value):
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return Decimal(match.group()) if match else None


def threshold_setting(value, label):
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.\d{1,3})?", text):
        raise ValueError(f"{label} must be a positive kg value with up to 3 decimal places")
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"Enter a valid {label.lower()}") from error
    if not 0 < amount <= Decimal("999999999.999"):
        raise ValueError(f"{label} must be greater than 0 and at most 999999999.999 kg")
    return format(amount, "f")

def furnace_prefix(name):
    value = str(name or "").strip()
    trailing = re.search(r"([A-Za-z])\s*$", value)
    if trailing:
        return trailing.group(1).upper()
    first = re.search(r"[A-Za-z0-9]", value)
    return first.group(0).upper() if first else "M"


@main_bp.get("/api/melt-number-settings")
def melt_number_settings():
    with database_connection() as connection:
        furnaces = connection.execute(
            "SELECT id,image_name,melt_start_serial FROM product_images WHERE image_type='furnace' ORDER BY id"
        ).fetchall()
        items = []
        for furnace in furnaces:
            next_serial, _ = next_melt_serial(connection, furnace["id"], furnace["melt_start_serial"])
            items.append({
                "furnace_id": furnace["id"],
                "furnace_name": furnace["image_name"],
                "start_serial": furnace["melt_start_serial"],
                "next_serial": next_serial,
                "next_melt_number": f'{furnace_prefix(furnace["image_name"])}{next_serial}',
            })
    return jsonify(items)


@main_bp.route("/api/melt-threshold-settings", methods=["GET", "PUT"])
def melt_threshold_settings():
    with database_connection() as connection:
        row = connection.execute(
            "SELECT individual_threshold_kg, melt_threshold_kg FROM melt_threshold_settings WHERE id=1"
        ).fetchone()
    if request.method == "GET":
        return jsonify(dict(row) if row else {"individual_threshold_kg": None, "melt_threshold_kg": None})

    payload = request.get_json(silent=True) or {}
    try:
        individual_threshold = threshold_setting(payload.get("individual_threshold_kg"), "Individual threshold")
        melt_threshold = threshold_setting(payload.get("melt_threshold_kg"), "Total melt threshold")
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    with database_connection() as connection:
        connection.execute("""
            INSERT INTO melt_threshold_settings (
                id, individual_threshold_kg, melt_threshold_kg, updated_at
            ) VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                individual_threshold_kg=excluded.individual_threshold_kg,
                melt_threshold_kg=excluded.melt_threshold_kg,
                updated_at=excluded.updated_at
        """, (individual_threshold, melt_threshold, updated_at))
    before = dict(row) if row else {}
    after = {"individual_threshold_kg": individual_threshold, "melt_threshold_kg": melt_threshold}
    record_changes("melt_threshold_settings", 1, "update", before, after)
    return jsonify({"ok": True, **after})


@main_bp.put("/api/melt-number-settings/<int:furnace_id>")
def update_melt_number_setting(furnace_id):
    payload = request.get_json(silent=True) or {}
    value = payload.get("start_serial")
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value or "")):
        return jsonify({"ok": False, "error": "Enter a positive starting serial number"}), 400
    start_serial = int(value)
    if start_serial > 999999999:
        return jsonify({"ok": False, "error": "Starting serial must be 999999999 or less"}), 400
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        furnace = connection.execute(
            "SELECT id,image_name,melt_start_serial FROM product_images WHERE id=? AND image_type='furnace'",
            (furnace_id,),
        ).fetchone()
        if furnace is None:
            return jsonify({"ok": False, "error": "Furnace not found"}), 404
        previous_serial = furnace["melt_start_serial"]
        _, last_used = next_melt_serial(connection, furnace_id, furnace["melt_start_serial"])
        if start_serial != previous_serial and start_serial <= last_used:
            return jsonify({"ok": False, "error": f"Choose {last_used + 1} or higher; earlier melt numbers are already used"}), 400
        connection.execute(
            "UPDATE product_images SET melt_start_serial=? WHERE id=?",
            (start_serial, furnace_id),
        )
        next_serial, _ = next_melt_serial(connection, furnace_id, start_serial)
    if start_serial != previous_serial:
        record_changes("melt_number_settings", furnace_id, "edit", {"start_serial": previous_serial}, {"start_serial": start_serial})
    return jsonify({"ok": True, "furnace_id": furnace_id, "start_serial": start_serial, "next_serial": next_serial, "next_melt_number": f'{furnace_prefix(furnace["image_name"])}{next_serial}'})

def melt_totals(connection, furnace_id, melt_number):
    rows = connection.execute(
        "SELECT weight FROM weight_captures WHERE furnace_id=? AND melt_number=?",
        (str(furnace_id), melt_number),
    ).fetchall()
    return {"melt_count": len(rows), "melt_total_weight": sum(numeric_weight(row["weight"]) for row in rows)}

@main_bp.post("/api/weight-captures")
def create_weight_capture():
    payload = request.get_json(silent=True) or {}; weight = str(payload.get("weight", "")).strip()
    try: image_id = int(payload.get("product_image_id"))
    except (TypeError, ValueError): return jsonify({"ok": False, "error": "Select a valid product image"}), 400
    try: furnace_id=int(payload.get("furnace_id"))
    except (TypeError,ValueError): return jsonify({"ok": False, "error": "Select a valid furnace before selecting an image"}), 400
    if not weight or weight == "------": return jsonify({"ok": False, "error": "Wait for a live weight before selecting an image"}), 400
    amount = weight_amount(weight)
    if amount is None or amount < 0:
        return jsonify({"ok": False, "error": "Wait for a valid non-negative weight before selecting an image"}), 400
    with database_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        furnace=connection.execute("SELECT id,image_name AS name,image_url,melt_start_serial FROM product_images WHERE id=? AND image_type='furnace'",(furnace_id,)).fetchone()
        if furnace is None: return jsonify({"ok": False, "error": "The selected furnace no longer exists"}),404
        image = connection.execute("SELECT id,image_name,image_url FROM product_images WHERE id=? AND image_type='product' AND furnace_id=?", (image_id,furnace_id)).fetchone()
        if image is None: return jsonify({"ok": False, "error": "The selected product image no longer exists"}), 404
        melt_number = str(payload.get("melt_number", "")).strip().upper()
        try: melt_serial = int(payload.get("melt_serial")) if payload.get("melt_serial") not in (None, "") else None
        except (TypeError, ValueError): melt_serial = None
        prefix = furnace_prefix(furnace["name"])
        if not melt_number or melt_serial is None:
            melt_serial, _ = next_melt_serial(connection, furnace_id, furnace["melt_start_serial"])
            melt_number = f"{prefix}{melt_serial}"
        elif melt_number != f"{prefix}{melt_serial}":
            return jsonify({"ok": False, "error": "The active melt number does not match this furnace"}), 400
        settings = connection.execute(
            "SELECT individual_threshold_kg, melt_threshold_kg FROM melt_threshold_settings WHERE id=1"
        ).fetchone()
        if settings and settings["individual_threshold_kg"] is not None:
            limit = Decimal(settings["individual_threshold_kg"])
            if amount > limit:
                return jsonify({"ok": False, "code": "threshold_exceeded", "error":
                    f"Individual weight {amount} kg exceeds the {limit} kg limit. Contact admin."}), 409
        if settings and settings["melt_threshold_kg"] is not None:
            limit = Decimal(settings["melt_threshold_kg"])
            rows = connection.execute(
                "SELECT weight FROM weight_captures WHERE furnace_id=? AND melt_number=?",
                (str(furnace_id), melt_number),
            ).fetchall()
            current_total = sum((weight_amount(row["weight"]) or Decimal(0) for row in rows), Decimal(0))
            if current_total + amount > limit:
                return jsonify({"ok": False, "code": "threshold_exceeded", "error":
                    f"Melt {melt_number} would total {current_total + amount} kg, above the {limit} kg limit. Contact admin."}), 409
        captured_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        cursor = connection.execute("INSERT INTO weight_captures (product_image_id,image_name,image_url,weight,furnace_id,furnace_name,furnace_image_url,melt_number,melt_serial,captured_at,captured_by_user_id,captured_by_username) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (image["id"],image["image_name"],image["image_url"],weight,str(furnace_id),furnace["name"],furnace["image_url"],melt_number,melt_serial,captured_at,session["user_id"],session["username"]))
        totals = melt_totals(connection, furnace_id, melt_number)
    return jsonify({"ok": True,"id":cursor.lastrowid,"image_name":image["image_name"],"image_url":image["image_url"],"weight":weight,"furnace_id":furnace_id,"furnace_name":furnace["name"],"furnace_image_url":furnace["image_url"],"melt_number":melt_number,"melt_serial":melt_serial,**totals,"captured_at":captured_at}), 201

@main_bp.post("/api/melts/complete")
def complete_melt():
    payload = request.get_json(silent=True) or {}
    try: furnace_id = int(payload.get("furnace_id"))
    except (TypeError, ValueError): return jsonify({"ok": False, "error": "Select a valid furnace"}), 400
    melt_number = str(payload.get("melt_number", "")).strip().upper()
    capture_ids = payload.get("capture_ids") or []
    if not melt_number: return jsonify({"ok": False, "error": "No active melt to complete"}), 400
    if not isinstance(capture_ids, list) or not capture_ids: return jsonify({"ok": False, "error": "Add at least one product before completing the melt"}), 400
    try: capture_ids = [int(item) for item in capture_ids]
    except (TypeError, ValueError): return jsonify({"ok": False, "error": "Invalid melt captures"}), 400
    placeholders = ",".join("?" for _ in capture_ids)
    completed_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
    with database_connection() as connection:
        rows = connection.execute(
            f"SELECT id,weight FROM weight_captures WHERE id IN ({placeholders}) AND furnace_id=? AND melt_number=? AND captured_by_user_id=?",
            (*capture_ids, str(furnace_id), melt_number, session["user_id"]),
        ).fetchall()
        if len(rows) != len(capture_ids): return jsonify({"ok": False, "error": "The active melt captures could not be verified"}), 400
        connection.execute(
            f"UPDATE weight_captures SET melt_completed_at=? WHERE id IN ({placeholders})",
            (completed_at, *capture_ids),
        )
        total_weight = sum(numeric_weight(row["weight"]) for row in rows)
    return jsonify({"ok": True, "melt_number": melt_number, "melt_count": len(rows), "melt_total_weight": total_weight, "melt_completed_at": completed_at})

@main_bp.post("/api/weight-captures/<int:capture_id>/cancel")
def cancel_weight_capture(capture_id):
    with database_connection() as connection:
        capture = connection.execute("SELECT id,captured_by_user_id,captured_at FROM weight_captures WHERE id=?", (capture_id,)).fetchone()
        if capture is None: return jsonify({"ok":False,"error":"Capture not found or already cancelled"}),404
        if capture["captured_by_user_id"] != session["user_id"]: return jsonify({"ok":False,"error":"You can only cancel your own capture"}),403
        captured_time=datetime.fromisoformat(capture["captured_at"])
        if (datetime.now().astimezone()-captured_time).total_seconds()>15: return jsonify({"ok":False,"error":"The cancellation period has expired"}),400
        connection.execute("DELETE FROM weight_captures WHERE id=?",(capture_id,))
    return jsonify({"ok":True,"id":capture_id})
