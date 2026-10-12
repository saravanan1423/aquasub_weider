import ipaddress
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid


class WifiError(RuntimeError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


_change_lock = threading.Lock()


def parse_nmcli(output):
    """Parse nmcli's escaped, colon-delimited terse records."""
    rows = []
    for line in output.splitlines():
        fields, value, escaped = [], [], False
        for char in line:
            if escaped:
                value.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == ":":
                fields.append("".join(value))
                value = []
            else:
                value.append(char)
        if escaped:
            value.append("\\")
        rows.append(fields + ["".join(value)])
    return rows


def run_nmcli(*args, input_text=None, wait=10):
    executable = shutil.which("nmcli") if sys.platform.startswith("linux") else None
    if not executable:
        raise WifiError("Wi-Fi settings require Raspberry Pi OS with NetworkManager (nmcli).")
    try:
        result = subprocess.run(
            [executable, "--colors", "no", "--wait", str(wait), *args],
            input=input_text, capture_output=True, text=True, timeout=wait + 10,
            env={**os.environ, "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired:
        raise WifiError("Wi-Fi operation timed out. Refresh the status and try again.", 504) from None
    except OSError:
        raise WifiError("Could not run NetworkManager on this device.") from None
    if result.returncode:
        details=(result.stderr + result.stdout).lower()
        if "not authorized" in details or "permission denied" in details or "insufficient privileges" in details:
            raise WifiError("The app user needs NetworkManager permission to manage Wi-Fi. Contact admin.", 403)
        # Do not expose nmcli output: the interactive editor can echo secrets.
        raise WifiError("Wi-Fi operation failed. Check the password, network availability and NetworkManager service.", 502)
    return result.stdout


def wifi_interface():
    devices=parse_nmcli(run_nmcli("--terse", "--escape", "yes", "--fields", "DEVICE,TYPE,STATE", "device", "status"))
    wireless=[row for row in devices if len(row) == 3 and row[1] == "wifi"]
    if not wireless:
        raise WifiError("No Wi-Fi adapter is available on this device.")
    return next((row[0] for row in wireless if row[2] == "connected"), wireless[0][0])


def profile_details(profile_id):
    try:
        profile_id=str(uuid.UUID(profile_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Select a valid Wi-Fi connection") from None
    fields="connection.type,connection.autoconnect,802-11-wireless.ssid,802-11-wireless.mode"
    rows=parse_nmcli(run_nmcli("--terse", "--escape", "yes", "--fields", fields, "connection", "show", "uuid", profile_id))
    data={row[0]: row[1] for row in rows if len(row) == 2}
    if data.get("connection.type") != "802-11-wireless" or data.get("802-11-wireless.mode") not in {"infrastructure", "--", ""}:
        raise ValueError("Select a Wi-Fi client connection")
    return {"id":profile_id, "ssid":data.get("802-11-wireless.ssid", ""),
            "autoconnect":data.get("connection.autoconnect") == "yes"}


def wifi_status():
    interface=wifi_interface()
    rows=parse_nmcli(run_nmcli("--terse", "--escape", "yes", "--fields", "GENERAL.STATE,GENERAL.CON-UUID,IP4.ADDRESS", "device", "show", interface))
    fields={row[0]:row[1] for row in rows if len(row) == 2}
    connected=fields.get("GENERAL.STATE", "").startswith("100 ")
    addresses=[]
    for key, value in fields.items():
        if key.startswith("IP4.ADDRESS"):
            try:
                address=ipaddress.ip_interface(value).ip
            except ValueError:
                continue
            if address.version == 4 and not address.is_loopback and not address.is_unspecified:
                addresses.append(str(address))
    profile=None
    if connected and fields.get("GENERAL.CON-UUID") not in {None, "", "--"}:
        profile=profile_details(fields["GENERAL.CON-UUID"])
    return {"interface":interface, "connected":connected,
            "ssid":profile["ssid"] if profile else "",
            "profile_id":profile["id"] if profile else None,
            "autoconnect":profile["autoconnect"] if profile else False,
            "ip_addresses":addresses if connected else []}


def wifi_networks():
    interface=wifi_interface()
    rows=parse_nmcli(run_nmcli("--terse", "--escape", "yes", "--fields", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", interface, "--rescan", "yes", wait=20))
    networks={}
    for row in rows:
        if len(row) != 4 or not row[1]:
            continue
        try:
            signal=max(0, min(100, int(row[2])))
        except ValueError:
            signal=0
        network={"ssid":row[1], "signal":signal, "security":row[3], "connected":row[0] == "*"}
        key=(row[1], row[3])
        previous=networks.get(key)
        if previous is None or (network["connected"], signal) > (previous["connected"], previous["signal"]):
            networks[key]=network
    return {"interface":interface, "networks":sorted(networks.values(), key=lambda item:(not item["connected"], -item["signal"], item["ssid"]))}


def validate_connection(payload):
    if not isinstance(payload, dict):
        raise ValueError("Enter valid Wi-Fi settings")
    ssid=payload.get("ssid")
    password=payload.get("password", "")
    autoconnect=payload.get("autoconnect", True)
    hidden=payload.get("hidden", False)
    if not isinstance(ssid, str) or not 1 <= len(ssid.encode("utf-8")) <= 32 or any(ord(char) < 32 or ord(char) == 127 for char in ssid):
        raise ValueError("Network name must be between 1 and 32 bytes without control characters")
    if not isinstance(password, str) or any(ord(char) < 32 or ord(char) == 127 for char in password):
        raise ValueError("Enter a valid Wi-Fi password")
    if password and not (8 <= len(password) <= 63 or re.fullmatch(r"[0-9a-fA-F]{64}", password)):
        raise ValueError("Wi-Fi password must be 8 to 63 characters, or a 64-character hexadecimal key")
    if not isinstance(autoconnect, bool) or not isinstance(hidden, bool):
        raise ValueError("Auto-connect and hidden network must be on or off")
    return ssid, password, autoconnect, hidden


def saved_networks(ssid):
    profiles=[]
    for row in parse_nmcli(run_nmcli("--terse", "--fields", "UUID,TYPE", "connection", "show")):
        if len(row) == 2 and row[1] == "802-11-wireless":
            try:
                candidate=profile_details(row[0])
            except ValueError:
                continue
            if candidate["ssid"] == ssid:
                profiles.append(candidate)
    return profiles


def connect_wifi(payload):
    ssid, password, autoconnect, hidden=validate_connection(payload)
    if not _change_lock.acquire(blocking=False):
        raise WifiError("Another Wi-Fi change is in progress. Wait and try again.", 409)
    created=None
    activated=False
    try:
        interface=wifi_interface()
        run_nmcli("radio", "wifi", "on")
        profiles=saved_networks(ssid)
        saved=profiles[0] if profiles else None
        if saved and not password:
            profile_id=saved["id"]
        else:
            security="WPA2" if hidden and password else ""
            if not hidden:
                matches=[item for item in wifi_networks()["networks"] if item["ssid"] == ssid]
                if not matches:
                    raise ValueError("Network is not available. Refresh or select Hidden network.")
                security=matches[0]["security"]
            if "802.1X" in security or "WEP" in security or "EAP" in security:
                raise ValueError("Set up enterprise or WEP networks in Raspberry Pi network settings first.")
            secured="WPA" in security
            if secured and not password:
                raise ValueError("Enter the Wi-Fi password for this network")
            if not secured and password:
                raise ValueError("This open network does not require a password")
            profile_id=str(uuid.uuid4())
            args=["connection", "add", "type", "wifi", "ifname", interface,
                  "con-name", f"Weider Wi-Fi {ssid}", "ssid", ssid,
                  "connection.uuid", profile_id, "connection.autoconnect", "no",
                  "802-11-wireless.hidden", "yes" if hidden else "no"]
            if secured:
                args += ["wifi-sec.key-mgmt", "sae" if "WPA3" in security and "WPA2" not in security else "wpa-psk"]
            run_nmcli(*args)
            created=profile_id
            if password:
                # Supply secrets through stdin, never command arguments or app storage.
                run_nmcli("connection", "edit", "uuid", profile_id,
                          input_text=f"set wifi-sec.psk {password}\nsave persistent\nquit\n")
        run_nmcli("connection", "up", "uuid", profile_id, "ifname", interface, wait=30)
        activated=True
        run_nmcli("connection", "modify", "uuid", profile_id, "connection.autoconnect", "yes" if autoconnect else "no")
        # An older profile for the same SSID must not override the user's choice.
        for previous in profiles:
            if previous["id"] != profile_id and previous["autoconnect"]:
                run_nmcli("connection", "modify", "uuid", previous["id"], "connection.autoconnect", "no")
        return {"profile_id":profile_id, "ssid":ssid, "autoconnect":autoconnect}
    except Exception:
        if created and not activated:
            try:
                run_nmcli("connection", "delete", "uuid", created)
            except WifiError:
                pass
        raise
    finally:
        _change_lock.release()


def set_autoconnect(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("autoconnect"), bool):
        raise ValueError("Auto-connect must be on or off")
    if not _change_lock.acquire(blocking=False):
        raise WifiError("Another Wi-Fi change is in progress. Wait and try again.", 409)
    try:
        status=wifi_status()
        if not status["connected"] or payload.get("profile_id") != status["profile_id"]:
            raise ValueError("The connected Wi-Fi network changed. Refresh and try again.")
        run_nmcli("connection", "modify", "uuid", status["profile_id"], "connection.autoconnect", "yes" if payload["autoconnect"] else "no")
        for previous in saved_networks(status["ssid"]):
            if previous["id"] != status["profile_id"] and previous["autoconnect"]:
                run_nmcli("connection", "modify", "uuid", previous["id"], "connection.autoconnect", "no")
        return {"profile_id":status["profile_id"], "autoconnect":payload["autoconnect"]}
    finally:
        _change_lock.release()
