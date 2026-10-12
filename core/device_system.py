import ipaddress
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


WINDOW_TITLE = "Device Settings - WeightFlow"


class DesktopError(RuntimeError):
    pass


def connected_network():
    """Read active IPv4 addresses without changing network configuration."""
    executable=shutil.which("ip") if sys.platform.startswith("linux") else None
    if not executable:
        return {"ip_address":None, "interface":None, "addresses":[]}
    try:
        result=subprocess.run([executable, "-json", "-4", "address", "show", "up"],
                              capture_output=True, text=True, check=True, timeout=5)
        interfaces=json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"ip_address":None, "interface":None, "addresses":[]}
    addresses=[]
    for interface in interfaces:
        if interface.get("operstate") != "UP" and "LOWER_UP" not in interface.get("flags", []):
            continue
        for record in interface.get("addr_info", []):
            if record.get("family") != "inet":
                continue
            try:
                address=ipaddress.ip_address(record.get("local", ""))
            except ValueError:
                continue
            if address.version != 4 or address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
                continue
            addresses.append({"interface":interface["ifname"], "ip_address":str(address)})
    addresses.sort(key=lambda item:(not item["interface"].startswith(("wlan", "wl")), item["interface"], item["ip_address"]))
    primary=addresses[0] if addresses else {"ip_address":None, "interface":None}
    return {**primary, "addresses":addresses}


def desktop_environment():
    if not sys.platform.startswith("linux"):
        raise DesktopError("The desktop top bar can only be opened on the Raspberry Pi.")
    environment=os.environ.copy()
    runtime=Path(environment.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    if runtime.is_dir():
        environment["XDG_RUNTIME_DIR"]=str(runtime)
    if not environment.get("WAYLAND_DISPLAY") and not environment.get("DISPLAY"):
        socket=next((path for path in sorted(runtime.glob("wayland-*")) if path.is_socket()), None)
        if socket:
            environment["WAYLAND_DISPLAY"]=socket.name
        elif Path("/tmp/.X11-unix/X0").exists():
            environment["DISPLAY"]=":0"
        else:
            raise DesktopError("No Raspberry Pi desktop session is available. Run the app as the desktop user.")
    authority=Path.home() / ".Xauthority"
    if environment.get("DISPLAY") and not environment.get("XAUTHORITY") and authority.is_file():
        environment["XAUTHORITY"]=str(authority)
    return environment


def desktop_command(command, environment):
    try:
        return subprocess.run(command, env=environment, capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        raise DesktopError("Could not access the desktop. Run the app in the Pi desktop user's session.") from None


def browser_app_id(value):
    value=value.lower()
    return any(name in value for name in ("chromium", "google-chrome", "chrome", "firefox", "msedge", "microsoft-edge"))


def ensure_panel(environment, wayland):
    name="wf-panel-pi" if wayland else "lxpanel"
    panel=shutil.which(name)
    pgrep=shutil.which("pgrep")
    if not panel or not pgrep:
        raise DesktopError(f"The Raspberry Pi desktop panel ({name}) and procps must be installed.")
    try:
        running=subprocess.run([pgrep, "-u", str(os.getuid()), "-x", name],
                               env=environment, capture_output=True, timeout=5)
        if running.returncode == 0:
            return
        if running.returncode != 1:
            raise DesktopError("Could not check the desktop panel.")
        command=[panel] if wayland else [panel, "--profile", "LXDE-pi"]
        subprocess.Popen(command, env=environment, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise DesktopError("Could not open the Raspberry Pi desktop panel.") from None


def open_top_bar():
    environment=desktop_environment()
    wayland=bool(environment.get("WAYLAND_DISPLAY"))
    if wayland:
        executable=shutil.which("wlrctl")
        if not executable:
            raise DesktopError("Install wlrctl on the Raspberry Pi to open the top bar from the app.")
        windows=desktop_command([executable, "toplevel", "list"], environment)
        selected=None
        for line in windows.splitlines():
            app_id, separator, title=line.partition(": ")
            if separator and browser_app_id(app_id) and title.startswith(WINDOW_TITLE):
                selected=(app_id, title)
                break
        if selected is None:
            raise DesktopError("Open Device Settings in the Pi's local browser before opening the top bar.")
        ensure_panel(environment, True)
        desktop_command([executable, "toplevel", "minimize", f"app_id:{selected[0]}", f"title:{selected[1]}"], environment)
    else:
        executable=shutil.which("xdotool")
        if not executable:
            raise DesktopError("Install xdotool on the Raspberry Pi to open the top bar from the app.")
        windows=desktop_command([executable, "search", "--onlyvisible", "--name", f"^{WINDOW_TITLE}"], environment)
        selected=None
        for window in windows.splitlines():
            if window.isdigit() and browser_app_id(desktop_command([executable, "getwindowclassname", window], environment).strip()):
                selected=window
                break
        if selected is None:
            raise DesktopError("Open Device Settings in the Pi's local browser before opening the top bar.")
        ensure_panel(environment, False)
        desktop_command([executable, "windowminimize", selected], environment)
    return {"message":"Desktop top bar opened. Restore the browser from the taskbar to return to the app."}
