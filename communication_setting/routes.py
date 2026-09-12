from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, session
from serial.tools import list_ports

from communication_setting.serial_monitor import BYTE_SIZES, PARITIES, STOP_BITS, monitor
from core.database import database_connection
from core.audit import record_changes

communication_bp = Blueprint("communication", __name__)


def validated_config(payload):
    config = {"port": str(payload.get("port", "")).strip(), "baud_rate": int(payload.get("baud_rate", 9600)), "data_bits": int(payload.get("data_bits", 8)), "parity": str(payload.get("parity", "none")).lower(), "stop_bits": str(payload.get("stop_bits", "1")), "frame_gap": max(.05, min(float(payload.get("frame_gap", .25)), 30)), "frame_timeout": max(.1, min(float(payload.get("frame_timeout", 1)), 30)), "start_marker": str(payload.get("start_marker", "$")), "end_marker": str(payload.get("end_marker", "]")), "start_address": max(int(payload.get("start_address", 5)), 1), "end_address": max(int(payload.get("end_address", 10)), 1)}
    if not config["port"]: raise ValueError("Choose or enter a serial port")
    if config["data_bits"] not in BYTE_SIZES or config["parity"] not in PARITIES or config["stop_bits"] not in STOP_BITS: raise ValueError("Invalid serial configuration")
    if not 1 <= config["baud_rate"] <= 4_000_000: raise ValueError("Baud rate must be between 1 and 4,000,000")
    if not config["start_marker"] or not config["end_marker"]: raise ValueError("Start and end characters are required")
    if config["end_address"] < config["start_address"]: raise ValueError("End address must be greater than or equal to start address")
    return config


def save_settings(config):
    with database_connection() as connection:
        existing = connection.execute("SELECT * FROM serial_settings WHERE id=1").fetchone()
        tracked = ("port","baud_rate","data_bits","parity","stop_bits","start_marker","end_marker","start_address","end_address","frame_timeout","created_by")
        before = {field: existing[field] for field in tracked} if existing else {}
        connection.execute("""INSERT INTO serial_settings (id,port,baud_rate,data_bits,parity,stop_bits,start_marker,end_marker,start_address,end_address,frame_timeout,created_by,updated_at) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET port=excluded.port,baud_rate=excluded.baud_rate,data_bits=excluded.data_bits,parity=excluded.parity,stop_bits=excluded.stop_bits,start_marker=excluded.start_marker,end_marker=excluded.end_marker,start_address=excluded.start_address,end_address=excluded.end_address,frame_timeout=excluded.frame_timeout,created_by=excluded.created_by,updated_at=excluded.updated_at""", (config["port"],config["baud_rate"],config["data_bits"],config["parity"],config["stop_bits"],config["start_marker"],config["end_marker"],config["start_address"],config["end_address"],config["frame_timeout"],session["username"],datetime.now().astimezone().isoformat(timespec="seconds")))
    after = {field: (session["username"] if field == "created_by" else config[field]) for field in tracked}
    record_changes("communication_settings", 1, "update", before, after)


@communication_bp.get("/communication")
def page(): return render_template("index.html")

@communication_bp.get("/api/ports")
def ports(): return jsonify([{"device": port.device, "description": port.description or "Serial device"} for port in sorted(list_ports.comports(), key=lambda item: item.device)])

@communication_bp.route("/api/settings", methods=["GET", "POST"])
def settings():
    if request.method == "GET":
        with database_connection() as connection: row = connection.execute("SELECT * FROM serial_settings WHERE id=1").fetchone()
        return jsonify(dict(row) if row else {})
    try: config = validated_config(request.get_json(silent=True) or {}); save_settings(config)
    except (TypeError, ValueError) as error: return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True, "message": "Settings saved"})

@communication_bp.post("/api/connect")
def connect():
    try: config = validated_config(request.get_json(silent=True) or {}); save_settings(config); monitor.start(config)
    except (TypeError, ValueError) as error: return jsonify({"ok": False, "error": str(error)}), 400
    return jsonify({"ok": True})

@communication_bp.post("/api/disconnect")
def disconnect(): monitor.stop(); return jsonify({"ok": True})

@communication_bp.post("/api/clear")
def clear(): monitor.clear(); return jsonify({"ok": True})

@communication_bp.get("/api/data")
def data():
    try: after = max(int(request.args.get("after", 0)), 0)
    except ValueError: after = 0
    return jsonify(monitor.snapshot(after))
