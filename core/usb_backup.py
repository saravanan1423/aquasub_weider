import json
import hashlib
import errno
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

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


def backup_shift_reports(reports, drive_id, job_key):
    # A stable directory and checksum manifest make crash retries idempotent.
    key=uuid.UUID(job_key).hex
    drive=resolve_drive(drive_id)
    root=Path(drive["mountpoint"])
    if not root.is_mount():
        raise OSError("USB drive was removed during backup")
    day=datetime.fromisoformat(reports["melt"]["range_from"]).strftime("%Y%m%d_%H%M")
    name=f"Shift_Backup_{day}_{key}"
    folder=root / "Weider Reports" / name
    payload_hash=hashlib.sha256(json.dumps(reports, sort_keys=True).encode("utf-8")).hexdigest()
    filenames=["Melt_Summary.csv", "Melt_Summary.pdf", "Individual_Captures.csv", "Individual_Captures.pdf"]
    if folder.exists():
        try:
            manifest=json.loads((folder / ".backup.json").read_text(encoding="utf-8"))
            valid=(manifest["job_key"] == key and manifest["payload_hash"] == payload_hash
                   and set(manifest["files"]) == set(filenames)
                   and all(hashlib.sha256((folder / file).read_bytes()).hexdigest() == manifest["files"][file] for file in filenames))
        except (OSError, ValueError, KeyError, TypeError):
            valid=False
        if not valid:
            raise OSError("Existing shift backup is incomplete or damaged. Use another USB drive or contact admin.")
        if not root.is_mount():
            raise OSError("USB drive was removed during backup")
        sync_directory(folder.parent)
        return {"drive":drive["label"], "folder":str(folder), "files":filenames}
    files={}
    for report_type, prefix in (("melt", "Melt_Summary"), ("individual", "Individual_Captures")):
        files[f"{prefix}.csv"]=report_csv(reports[report_type])
        files[f"{prefix}.pdf"]=report_pdf(reports[report_type])
    manifest={"job_key":key, "payload_hash":payload_hash,
              "files":{file:hashlib.sha256(content).hexdigest() for file, content in files.items()}}
    files[".backup.json"]=json.dumps(manifest, sort_keys=True).encode("utf-8")
    result=write_usb_files(drive, name, files)
    result["files"]=filenames
    return result
