import csv
import io
import ipaddress
import json
import re
import secrets
import socket
import struct
import sys
from datetime import date, datetime, time, timedelta
from urllib.parse import urlsplit
from flask import Blueprint, jsonify, render_template, request, send_file, session
import qrcode
from core.database import database_connection
from core.audit import record_changes

logs_bp = Blueprint("logs", __name__)


def usable_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError):
        return None
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
        return None
    return str(address)


def network_interface_addresses():
    if not sys.platform.startswith("linux"):
        return []
    import fcntl

    addresses = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        for _, name in socket.if_nameindex():
            try:
                details = fcntl.ioctl(probe.fileno(), 0x8915, struct.pack("256s", name[:15].encode()))
            except OSError:
                continue
            address = usable_ipv4(socket.inet_ntoa(details[20:24]))
            if address:
                addresses.append((name, address))
    return addresses


def report_share_host():
    peer = usable_ipv4(request.remote_addr)
    if peer:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect((peer, 9))
                address = usable_ipv4(probe.getsockname()[0])
                if address:
                    return address
        except OSError:
            pass

    requested = usable_ipv4(urlsplit(request.host_url).hostname)
    if requested:
        return requested

    try:
        addresses = network_interface_addresses()
    except OSError:
        addresses = []
    addresses.sort(key=lambda item: (not item[0].startswith(("wlan", "wl", "ap", "uap")), item[0]))
    if addresses:
        return addresses[0][1]

    try:
        return usable_ipv4(socket.gethostbyname(socket.gethostname()))
    except OSError:
        return None

@logs_bp.get("/logs")
def page(): return render_template("capture_logs.html")

@logs_bp.get("/api/weight-captures")
def captures():
    if "page" in request.args:
        try: page=max(1,int(request.args.get("page",1))); per_page=max(1,min(int(request.args.get("per_page",5)),50))
        except ValueError: page,per_page=1,5
        with database_connection() as connection:
            rows=connection.execute("SELECT id,product_image_id,image_name,weight,furnace_id,furnace_name,melt_number,captured_at,captured_by_username FROM weight_captures ORDER BY id DESC").fetchall()
            shifts=connection.execute("SELECT name,start_time,end_time FROM shifts ORDER BY start_time,name").fetchall()
        melts=[]; grouped={}
        for row in rows:
            capture=dict(row)
            key=(str(capture["furnace_id"] or ""),capture["melt_number"] or f'capture-{capture["id"]}')
            if key not in grouped:
                grouped[key]={"furnace_id":capture["furnace_id"],"furnace_name":capture["furnace_name"],"melt_number":capture["melt_number"] or "-","total_weight":0.0,"capture_count":0,"shift_type":shift_name_for_capture(capture["captured_at"],shifts),"captured_by_username":capture["captured_by_username"],"captured_at":capture["captured_at"],"captures":[]}
                melts.append(grouped[key])
            melt=grouped[key]; melt["capture_count"]+=1; melt["total_weight"]+=numeric_weight(capture["weight"]); melt["captures"].append(capture)
        total=len(melts); pages=max(1,(total+per_page-1)//per_page); page=min(page,pages)
        items=melts[(page-1)*per_page:page*per_page]
        return jsonify({"items":items,"total":total,"page":page,"per_page":per_page,"pages":pages})
    try: limit=max(1,min(int(request.args.get("limit",20)),200))
    except ValueError: limit=20
    with database_connection() as connection: rows=connection.execute("SELECT id,product_image_id,image_name,image_url,weight,furnace_id,furnace_name,furnace_image_url,melt_number,captured_at,captured_by_username FROM weight_captures ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
    return jsonify([dict(row) for row in rows])


def local_capture_datetime(value):
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    return parsed.astimezone() if parsed.tzinfo else parsed.astimezone()


def numeric_weight(value):
    match=re.search(r"[-+]?\d+(?:\.\d+)?",str(value).replace(",",""))
    return float(match.group()) if match else 0.0


def shift_name_for_capture(captured_at, shifts):
    captured_time=local_capture_datetime(captured_at).strftime("%H:%M")
    for shift in shifts:
        start,end=shift["start_time"],shift["end_time"]
        active=(start <= captured_time < end) if start <= end else (captured_time >= start or captured_time < end)
        if active: return shift["name"]
    return "Unassigned"


@logs_bp.get("/api/capture-report")
def capture_report():
    try:
        from_date=date.fromisoformat(request.args.get("from_date", ""))
        to_date=date.fromisoformat(request.args.get("to_date", ""))
    except ValueError: return jsonify({"ok":False,"error":"Select a valid From and To date"}),400
    if to_date < from_date: return jsonify({"ok":False,"error":"To date must be on or after From date"}),400
    shift_value=request.args.get("shift","all"); furnace_value=request.args.get("furnace","all")
    custom_mode=request.args.get("custom_time")=="1" or shift_value=="custom"
    if shift_value=="custom": shift_value="all"
    with database_connection() as connection:
        shifts=connection.execute("SELECT id,name,start_time,end_time FROM shifts ORDER BY start_time,name").fetchall()
        furnaces=connection.execute("SELECT furnace_id,furnace_name FROM (SELECT DISTINCT furnace_id,furnace_name FROM weight_captures WHERE furnace_id IS NOT NULL AND furnace_name IS NOT NULL UNION SELECT CAST(id AS TEXT),image_name FROM product_images WHERE image_type='furnace') ORDER BY furnace_name").fetchall()
        captures=connection.execute("SELECT id,furnace_id,furnace_name,melt_number,image_name,weight,captured_at,captured_by_username FROM weight_captures ORDER BY captured_at").fetchall()
    shift=None
    if shift_value != "all":
        try: shift=next((row for row in shifts if row["id"]==int(shift_value)),None)
        except ValueError: shift=None
        if shift is None: return jsonify({"ok":False,"error":"Select a valid shift"}),400
    custom_start=custom_end=None
    if custom_mode:
        try:
            custom_start=datetime.combine(from_date,time.fromisoformat(request.args.get("start_time",""))).astimezone()
            custom_end=datetime.combine(to_date,time.fromisoformat(request.args.get("end_time",""))).astimezone()
        except ValueError: return jsonify({"ok":False,"error":"Select valid custom start and end times"}),400
        if custom_end <= custom_start: return jsonify({"ok":False,"error":"Custom To date and time must be after From"}),400
    filtered=[]
    for capture in captures:
        captured=local_capture_datetime(capture["captured_at"]); captured_date=captured.date(); captured_time=captured.strftime("%H:%M")
        include=False
        if shift_value=="all": include=from_date <= captured_date <= to_date
        else:
            start,end=shift["start_time"],shift["end_time"]
            overnight=start>end
            shift_date=captured_date-timedelta(days=1) if overnight and captured_time<end else captured_date
            active=(start <= captured_time < end) if not overnight else (captured_time>=start or captured_time<end)
            include=active and from_date <= shift_date <= to_date
        if custom_mode: include=include and custom_start <= captured <= custom_end
        if furnace_value!="all" and str(capture["furnace_id"])!=furnace_value: include=False
        if include:
            item=dict(capture); item["captured_at"]=captured.isoformat(timespec="seconds"); item["numeric_weight"]=numeric_weight(capture["weight"]); item["shift_type"]=shift_name_for_capture(capture["captured_at"],shifts); filtered.append(item)
    summary={}
    for item in filtered:
        entry=summary.setdefault(item["image_name"],{"product":item["image_name"],"captures":0,"net_weight":0.0})
        entry["captures"]+=1; entry["net_weight"]+=item["numeric_weight"]
    melt_map={}
    for item in filtered:
        key=(str(item.get("furnace_id") or ""),item.get("melt_number") or f'capture-{item["id"]}')
        if key not in melt_map:
            melt_map[key]={"melt_number":item.get("melt_number") or "-","furnace_name":item.get("furnace_name") or "-","total_weight":0.0,"product_count":0,"shift_type":item["shift_type"],"captured_by_username":item.get("captured_by_username") or "Unknown","captured_at":item["captured_at"]}
        melt=melt_map[key]; melt["total_weight"]+=item["numeric_weight"]; melt["product_count"]+=1
        if item["captured_at"] > melt["captured_at"]:
            melt["captured_at"]=item["captured_at"]; melt["captured_by_username"]=item.get("captured_by_username") or "Unknown"; melt["shift_type"]=item["shift_type"]
    melt_items=sorted(melt_map.values(),key=lambda item:item["captured_at"],reverse=True)
    shift_name="All shifts" if shift_value=="all" else shift["name"]
    furnace_name="All furnaces" if furnace_value=="all" else next((row["furnace_name"] for row in furnaces if str(row["furnace_id"])==furnace_value),"Selected furnace")
    range_from=custom_start.isoformat(timespec="minutes") if custom_mode else from_date.isoformat()
    range_to=custom_end.isoformat(timespec="minutes") if custom_mode else to_date.isoformat()
    report={"ok":True,"generated_by":session["username"],"generated_on":datetime.now().astimezone().isoformat(timespec="seconds"),"range_from":range_from,"range_to":range_to,"shift":shift_name,"furnace":furnace_name,"items":melt_items,"summary":list(summary.values()),"total_melts":len(melt_items),"total_products":len(filtered),"total_net_weight":sum(item["numeric_weight"] for item in filtered),"options":{"shifts":[dict(row) for row in shifts],"furnaces":[dict(row) for row in furnaces]}}
    host = report_share_host()
    if not host:
        return jsonify({"ok": False, "error": "No network address is available for report sharing"}), 503
    token=secrets.token_urlsafe(18); expires_at=datetime.now().astimezone()+timedelta(minutes=15)
    port=str(urlsplit(request.host_url).port or request.environ.get("SERVER_PORT","5000"))
    authority=f"{host}:{port}" if port not in {"80","443"} else host
    download_url=f"{request.scheme}://{authority}/reports/download/{token}"
    with database_connection() as connection:
        connection.execute("DELETE FROM report_downloads WHERE expires_at<?",(datetime.now().astimezone().isoformat(),))
        connection.execute("INSERT INTO report_downloads (token,report_json,download_url,expires_at) VALUES (?,?,?,?)",(token,json.dumps(report),download_url,expires_at.isoformat()))
    report.update({"download_url":download_url,"qr_url":f"/api/capture-report-qr/{token}","download_expires_at":expires_at.isoformat()})
    return jsonify(report)


@logs_bp.get("/api/capture-report-options")
def capture_report_options():
    with database_connection() as connection:
        shifts=connection.execute("SELECT id,name,start_time,end_time FROM shifts ORDER BY start_time,name").fetchall()
        furnaces=connection.execute("SELECT furnace_id,furnace_name FROM (SELECT DISTINCT furnace_id,furnace_name FROM weight_captures WHERE furnace_id IS NOT NULL AND furnace_name IS NOT NULL UNION SELECT CAST(id AS TEXT),image_name FROM product_images WHERE image_type='furnace') ORDER BY furnace_name").fetchall()
    return jsonify({"shifts":[dict(row) for row in shifts],"furnaces":[dict(row) for row in furnaces]})


@logs_bp.get("/api/capture-report-qr/<token>")
def capture_report_qr(token):
    with database_connection() as connection: saved=connection.execute("SELECT download_url,expires_at FROM report_downloads WHERE token=?",(token,)).fetchone()
    if saved is None or datetime.fromisoformat(saved["expires_at"])<=datetime.now().astimezone(): return jsonify({"ok":False,"error":"Report download link has expired"}),404
    image=qrcode.make(saved["download_url"]); output=io.BytesIO(); image.save(output,format="PNG"); output.seek(0)
    return send_file(output,mimetype="image/png",max_age=0)


@logs_bp.get("/reports/download/<token>")
def download_shared_report(token):
    with database_connection() as connection: saved=connection.execute("SELECT report_json,expires_at FROM report_downloads WHERE token=?",(token,)).fetchone()
    if saved is None or datetime.fromisoformat(saved["expires_at"])<=datetime.now().astimezone(): return "This report download link has expired.",410
    report=json.loads(saved["report_json"]); output=io.StringIO(newline=""); writer=csv.writer(output,quoting=csv.QUOTE_ALL)
    writer.writerows([["MELT REPORT","","","","","","",""] ,["Generated by",report["generated_by"],"Generated on",report["generated_on"],"Shift",report["shift"],"",""] ,["From",report["range_from"],"To",report["range_to"],"Furnace",report["furnace"],"",""] ,[],["S.No","Furnace","Melt number","Shift type","Total weight (kg)","Products","Captured by","Last captured"]])
    for index,item in enumerate(report["items"],1): writer.writerow([index,item["furnace_name"],item["melt_number"],item["shift_type"],f'{item["total_weight"]:.3f}',item["product_count"],item["captured_by_username"],item["captured_at"]])
    writer.writerow([]); writer.writerow(["Grand total",report["total_melts"],"melts",f'{report["total_net_weight"]:.3f} kg',report["total_products"],"products",""])
    data=io.BytesIO(output.getvalue().encode("utf-8-sig")); data.seek(0)
    return send_file(data,mimetype="text/csv",as_attachment=True,download_name=f'capture-report-{datetime.now().date().isoformat()}.csv')

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
