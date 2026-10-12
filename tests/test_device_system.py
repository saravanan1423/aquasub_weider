import gc
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import app
from core import device_system
from core.database import database_connection, initialize_database


class DeviceSystemTests(unittest.TestCase):
    def addresses(self, interfaces):
        result=SimpleNamespace(stdout=json.dumps(interfaces))
        with patch.object(device_system.sys, "platform", "linux"), patch.object(device_system.shutil, "which", return_value="/usr/sbin/ip"), patch.object(device_system.subprocess, "run", return_value=result) as runner:
            state=device_system.connected_network()
        runner.assert_called_once_with(["/usr/sbin/ip", "-json", "-4", "address", "show", "up"], capture_output=True, text=True, check=True, timeout=5)
        return state

    def test_connected_ip_prefers_wifi_and_excludes_unusable_addresses(self):
        state=self.addresses([
            {"ifname":"eth0", "operstate":"UP", "addr_info":[{"family":"inet", "local":"10.0.0.2"}]},
            {"ifname":"wlan0", "operstate":"UP", "addr_info":[{"family":"inet", "local":"192.168.43.12"}, {"family":"inet", "local":"127.0.0.1"}, {"family":"inet", "local":"169.254.1.2"}, {"family":"inet6", "local":"::1"}]},
            {"ifname":"wlan1", "operstate":"DOWN", "addr_info":[{"family":"inet", "local":"192.168.1.9"}]},
        ])
        self.assertEqual((state["ip_address"], state["interface"]), ("192.168.43.12", "wlan0"))
        self.assertEqual(len(state["addresses"]), 2)

    def test_ethernet_fallback_and_disconnected_state(self):
        state=self.addresses([{"ifname":"eth0", "operstate":"UP", "addr_info":[{"family":"inet", "local":"10.0.0.2"}]}])
        self.assertEqual(state["ip_address"], "10.0.0.2")
        self.assertIsNone(self.addresses([])["ip_address"])

    def test_ip_command_failure_or_unsupported_platform_is_safe(self):
        with patch.object(device_system.sys, "platform", "linux"), patch.object(device_system.shutil, "which", return_value="/usr/sbin/ip"), patch.object(device_system.subprocess, "run", side_effect=subprocess.TimeoutExpired("ip", 5)):
            self.assertIsNone(device_system.connected_network()["ip_address"])
        with patch.object(device_system.sys, "platform", "win32"), patch.object(device_system.subprocess, "run") as runner:
            self.assertIsNone(device_system.connected_network()["ip_address"])
        runner.assert_not_called()

    def test_wayland_minimizes_only_weightflow_browser(self):
        title=f"{device_system.WINDOW_TITLE} - Chromium"
        windows=f"foot: Terminal\nchromium: Mail\nchromium: {title}\n"
        environment={"WAYLAND_DISPLAY":"wayland-0"}
        with patch.object(device_system, "desktop_environment", return_value=environment), patch.object(device_system.shutil, "which", return_value="/usr/bin/wlrctl"), patch.object(device_system, "desktop_command", side_effect=[windows, ""]) as command, patch.object(device_system, "ensure_panel") as panel:
            result=device_system.open_top_bar()
        panel.assert_called_once_with(environment, True)
        self.assertIn("top bar", result["message"])
        self.assertEqual(command.call_args_list[-1].args, (["/usr/bin/wlrctl", "toplevel", "minimize", "app_id:chromium", f"title:{title}"], environment))

    def test_wayland_no_app_window_never_minimizes_other_apps(self):
        with patch.object(device_system, "desktop_environment", return_value={"WAYLAND_DISPLAY":"wayland-0"}), patch.object(device_system.shutil, "which", return_value="/usr/bin/wlrctl"), patch.object(device_system, "desktop_command", return_value="chromium: Mail\nfoot: Terminal\n") as command, patch.object(device_system, "ensure_panel") as panel:
            with self.assertRaises(device_system.DesktopError):
                device_system.open_top_bar()
        self.assertEqual(command.call_count, 1)
        panel.assert_not_called()

    def test_x11_targets_browser_window_not_terminal(self):
        environment={"DISPLAY":":0"}
        with patch.object(device_system, "desktop_environment", return_value=environment), patch.object(device_system.shutil, "which", return_value="/usr/bin/xdotool"), patch.object(device_system, "desktop_command", side_effect=["123\n456\n", "Terminal", "Chromium", ""]) as command, patch.object(device_system, "ensure_panel") as panel:
            device_system.open_top_bar()
        panel.assert_called_once_with(environment, False)
        self.assertEqual(command.call_args_list[-1].args, (["/usr/bin/xdotool", "windowminimize", "456"], environment))

    def test_missing_window_tool_has_useful_error(self):
        with patch.object(device_system, "desktop_environment", return_value={"WAYLAND_DISPLAY":"wayland-0"}), patch.object(device_system.shutil, "which", return_value=None), self.assertRaises(device_system.DesktopError) as error:
            device_system.open_top_bar()
        self.assertIn("wlrctl", str(error.exception))

    def test_panel_is_not_started_twice(self):
        with patch.object(device_system.shutil, "which", side_effect=lambda name:f"/usr/bin/{name}"), patch.object(device_system.os, "getuid", return_value=1000, create=True), patch.object(device_system.subprocess, "run", return_value=SimpleNamespace(returncode=0)), patch.object(device_system.subprocess, "Popen") as launch:
            device_system.ensure_panel({}, True)
        launch.assert_not_called()

    def test_missing_panel_is_launched_with_fixed_arguments(self):
        with patch.object(device_system.shutil, "which", side_effect=lambda name:f"/usr/bin/{name}"), patch.object(device_system.os, "getuid", return_value=1000, create=True), patch.object(device_system.subprocess, "run", return_value=SimpleNamespace(returncode=1)), patch.object(device_system.subprocess, "Popen") as launch:
            device_system.ensure_panel({}, False)
        self.assertEqual(launch.call_args.args[0], ["/usr/bin/lxpanel", "--profile", "LXDE-pi"])
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        self.assertNotIn("shell", launch.call_args.kwargs)

    def test_desktop_without_session_and_windows_are_rejected(self):
        with patch.object(device_system.sys, "platform", "win32"), self.assertRaises(device_system.DesktopError):
            device_system.desktop_environment()
        with patch.object(device_system.sys, "platform", "linux"), patch.dict(device_system.os.environ, {}, clear=True), patch.object(device_system.os, "getuid", return_value=1000, create=True), patch.object(Path, "glob", return_value=[]), patch.object(Path, "exists", return_value=False), patch.object(Path, "is_dir", return_value=False), self.assertRaises(device_system.DesktopError):
            device_system.desktop_environment()


class DeviceSystemApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_path=app.config["DATABASE_PATH"]
        app.config["DATABASE_PATH"]=Path(self.temporary.name) / "test.db"
        initialize_database(app)
        with app.app_context():
            connection=database_connection()
            self.admin_id=connection.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
            self.operator_id=connection.execute("INSERT INTO users (username,password_hash,role,created_at) VALUES ('operator','unused','user','2026-10-12')").lastrowid
            connection.commit()
            connection.close()
        self.client=app.test_client()
        with self.client.session_transaction() as session:
            session.update(user_id=self.admin_id, username="admin")

    def tearDown(self):
        self.client=None
        app.config["DATABASE_PATH"]=self.original_path
        gc.collect()
        self.temporary.cleanup()

    def test_ip_endpoint_is_read_only_and_controls_are_removed(self):
        with patch.object(device_system, "connected_network", return_value={"ip_address":"192.168.43.12", "interface":"wlan0", "addresses":[]}):
            response=self.client.get("/api/device-network")
        self.assertEqual(response.json["ip_address"], "192.168.43.12")
        page=self.client.get("/device-settings")
        self.assertIn(b'id="connected-ip" readonly', page.data)
        self.assertIn(b'id="show-top-bar"', page.data)
        self.assertNotIn(b'id="wifi-tab"', page.data)
        self.assertNotIn(b'id="wifi-password"', page.data)
        for path, method in (("status","get"), ("networks","get"), ("connect","post"), ("autoconnect","put")):
            self.assertEqual(getattr(self.client, method)(f"/api/wifi/{path}").status_code, 404)

    def test_only_admin_can_read_network_or_open_top_bar(self):
        with self.client.session_transaction() as session:
            session.update(user_id=self.operator_id, username="operator")
        self.assertEqual(self.client.get("/api/device-network").status_code, 403)
        self.assertEqual(self.client.post("/api/desktop/top-bar").status_code, 403)

    def test_top_bar_success_failure_and_cross_origin_requests(self):
        with patch.object(device_system, "open_top_bar", return_value={"message":"Top bar opened"}):
            self.assertEqual(self.client.post("/api/desktop/top-bar").json["message"], "Top bar opened")
        with patch.object(device_system, "open_top_bar", side_effect=device_system.DesktopError("Install wlrctl")):
            response=self.client.post("/api/desktop/top-bar")
        self.assertEqual(response.status_code, 503)
        self.assertIn("wlrctl", response.json["error"])
        self.assertEqual(self.client.post("/api/desktop/top-bar", headers={"Origin":"https://another-site.test"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
