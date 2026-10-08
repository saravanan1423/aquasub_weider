import json
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


def backup_report(report, drive_id):
    drive = next((item for item in mounted_usb_drives() if item["id"] == drive_id), None)
    if drive is None:
        raise ValueError("USB drive is not available. Refresh the drive list and try again.")
    root = Path(drive["mountpoint"])
    csv_data, pdf_data = report_csv(report), report_pdf(report)
    if shutil.disk_usage(root).free < len(csv_data) + len(pdf_data) + 1024 * 1024:
        raise OSError("Not enough free space on the USB drive")
    if not root.is_mount():
        raise OSError("USB drive was removed during backup")
    parent = root / "Weider Reports"
    parent.mkdir(exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    name = f"Melt_Report_{timestamp}_{uuid.uuid4().hex[:6]}"
    staged, final = parent / f".{name}.partial", parent / name
    staged.mkdir()
    filenames = (f"{name}.csv", f"{name}.pdf")
    try:
        for filename, content in zip(filenames, (csv_data, pdf_data)):
            with (staged / filename).open("wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
        if not root.is_mount():
            raise OSError("USB drive was removed during backup")
        os.replace(staged, final)
    finally:
        if staged.exists():
            for filename in filenames:
                (staged / filename).unlink(missing_ok=True)
            staged.rmdir()
    return {"drive": drive["label"], "folder": str(final), "files": list(filenames),
            "total_melts": report["total_melts"], "total_weight": report["total_net_weight"]}
