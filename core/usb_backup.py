import calendar
import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

from core.report_export import report_csv, report_pdf


USB_MOUNT_ROOTS = (Path("/media"), Path("/mnt"), Path("/run/media"))


def mounted_usb_drives():
    if not sys.platform.startswith("linux"):
        return []
    try:
        result = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,TRAN,TYPE,MOUNTPOINT,LABEL,FSTYPE"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        devices = json.loads(result.stdout).get("blockdevices", [])
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
        return []

    drives = []

    def visit(device, usb=False):
        usb = usb or device.get("tran") == "usb"
        mount = device.get("mountpoint")
        if usb and mount:
            path = Path(mount).resolve()
            if (any(path.is_relative_to(root) and path != root for root in USB_MOUNT_ROOTS)
                    and path.is_mount() and os.access(path, os.W_OK)):
                try:
                    free_bytes = shutil.disk_usage(path).free
                except OSError:
                    free_bytes = 0
                drives.append({
                    "id": f"{device.get('name', '')}@{path}",
                    "label": device.get("label") or device.get("name") or path.name,
                    "mountpoint": str(path),
                    "free_bytes": free_bytes,
                })
        for child in device.get("children", []):
            visit(child, usb)

    for device in devices:
        visit(device)
    return drives


def resolve_drive(drive_id):
    drive = next((item for item in mounted_usb_drives() if item["id"] == drive_id), None)
    if drive is None:
        raise ValueError("USB drive is not available. Refresh the drive list and try again.")
    return drive


def sync_directory(path):
    if os.name != "posix":
        return
    descriptor=os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in {errno.EINVAL, errno.ENOTSUP}:
                raise
    finally:
        os.close(descriptor)


def write_usb_files(drive, name, files):
    root = Path(drive["mountpoint"])
    if shutil.disk_usage(root).free < sum(len(content) for content in files.values()) + 1024 * 1024:
        raise OSError("Not enough free space on the USB drive")
    if not root.is_mount():
        raise OSError("USB drive was removed during backup")
    parent = root / "Weider Reports"
    parent.mkdir(exist_ok=True)
    staged, final = parent / f".{name}.{uuid.uuid4().hex[:6]}.partial", parent / name
    staged.mkdir()
    try:
        for filename, content in files.items():
            with (staged / filename).open("wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
        if not root.is_mount():
            raise OSError("USB drive was removed during backup")
        sync_directory(staged)
        os.replace(staged, final)
        sync_directory(parent)
    finally:
        if staged.exists():
            for filename in files:
                (staged / filename).unlink(missing_ok=True)
            staged.rmdir()
    return {"drive": drive["label"], "folder": str(final), "files": list(files)}


def backup_report(reports, drive_id):
    drive=resolve_drive(drive_id)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    name = f"Reports_Backup_{timestamp}_{uuid.uuid4().hex[:6]}"
    files={}
    for report_type, prefix in (("melt", "Melt_Summary"), ("individual", "Individual_Captures")):
        files[f"{prefix}.csv"]=report_csv(reports[report_type])
        files[f"{prefix}.pdf"]=report_pdf(reports[report_type])
    report=reports["melt"]
    return {**write_usb_files(drive, name, files),
            "total_melts": report["total_melts"], "total_products": report["total_products"],
            "report_type": "both", "total_weight": report["total_net_weight"]}


REPORT_FILENAMES=("Melt_Summary.csv", "Melt_Summary.pdf", "Individual_Captures.csv", "Individual_Captures.pdf")


def scheduled_report_name(reports):
    report=reports["melt"]
    starts=datetime.fromisoformat(report["range_from"])
    schedule=report.get("backup_schedule", "shift")
    if schedule == "monthly":
        return f"meltreport_{calendar.month_name[starts.month]}_{starts.year}"
    name=f"meltreport_{starts.date().isoformat()}"
    if schedule == "shift":
        name += "_" + (secure_filename(report["shift"]) or "shift")
    return name


def report_payload_hash(reports):
    return hashlib.sha256(json.dumps(reports, sort_keys=True).encode("utf-8")).hexdigest()


def scheduled_report_files(reports, key):
    files={}
    for report_type, prefix in (("melt", "Melt_Summary"), ("individual", "Individual_Captures")):
        files[f"{prefix}.csv"]=report_csv(reports[report_type])
        files[f"{prefix}.pdf"]=report_pdf(reports[report_type])
    manifest={"job_key":key, "payload_hash":report_payload_hash(reports),
              "files":{file:hashlib.sha256(content).hexdigest() for file, content in files.items()}}
    files[".backup.json"]=json.dumps(manifest, sort_keys=True).encode("utf-8")
    return files


def verified_report_files(folder, reports, key):
    try:
        manifest_bytes=(folder / ".backup.json").read_bytes()
        manifest=json.loads(manifest_bytes)
        files={name:(folder / name).read_bytes() for name in REPORT_FILENAMES}
        valid=(manifest["job_key"] == key and manifest["payload_hash"] == report_payload_hash(reports)
               and set(manifest["files"]) == set(REPORT_FILENAMES)
               and all(hashlib.sha256(content).hexdigest() == manifest["files"][name] for name,content in files.items()))
        if not valid:
            raise ValueError("Report checksum mismatch")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise OSError("Existing report backup is incomplete or damaged. Contact admin.") from error
    return {**files, ".backup.json":manifest_bytes}


def report_destination(parent, reports, key):
    folder=parent / scheduled_report_name(reports)
    if folder.exists():
        try:
            owner=json.loads((folder / ".backup.json").read_bytes()).get("job_key")
        except (OSError, ValueError, AttributeError):
            owner=None
        if owner != key:
            # Keep distinct report periods with the same display name without overwriting.
            folder=parent / f"{folder.name}_{key}"
    return folder


def save_local_reports(reports, job_key):
    key=uuid.UUID(job_key).hex
    parent=Path(current_app.config.get("REPORT_STORAGE_DIR", Path(current_app.root_path) / "storage" / "reports"))
    parent.mkdir(parents=True, exist_ok=True)
    folder=report_destination(parent, reports, key)
    if folder.exists():
        verified_report_files(folder, reports, key)
        sync_directory(parent)
        return str(folder)
    files=scheduled_report_files(reports, key)
    with tempfile.TemporaryDirectory(prefix=f".{folder.name}.", suffix=".partial", dir=parent) as temporary:
        staged=Path(temporary) / "reports"
        staged.mkdir()
        for name, content in files.items():
            with (staged / name).open("wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
        sync_directory(staged)
        os.replace(staged, folder)
        sync_directory(parent)
    return str(folder)


def backup_shift_reports(reports, drive_id, job_key, local_folder=None):
    # A stable directory and checksum manifest make crash retries idempotent.
    key=uuid.UUID(job_key).hex
    drive=resolve_drive(drive_id)
    root=Path(drive["mountpoint"])
    if not root.is_mount():
        raise OSError("USB drive was removed during backup")
    day=datetime.fromisoformat(reports["melt"]["range_from"]).strftime("%Y%m%d_%H%M")
    schedule=reports["melt"].get("backup_schedule", "shift")
    prefix={"shift":"Shift", "daily":"Daily", "monthly":"Monthly"}.get(schedule, "Shift")
    legacy=root / "Weider Reports" / f"{prefix}_Backup_{day}_{key}"
    folder=legacy if legacy.exists() else report_destination(root / "Weider Reports", reports, key)
    filenames=list(REPORT_FILENAMES)
    if folder.exists():
        verified_report_files(folder, reports, key)
        if not root.is_mount():
            raise OSError("USB drive was removed during backup")
        sync_directory(folder.parent)
        return {"drive":drive["label"], "folder":str(folder), "files":filenames}
    files=verified_report_files(Path(local_folder), reports, key) if local_folder else scheduled_report_files(reports, key)
    result=write_usb_files(drive, folder.name, files)
    result["files"]=filenames
    return result
