import gc
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app import app
from core import wifi
from core.database import database_connection, initialize_database


PROFILE = "11111111-1111-4111-8111-111111111111"
NEW_PROFILE = "22222222-2222-4222-8222-222222222222"


class WifiTests(unittest.TestCase):
    def test_terse_records_preserve_escaped_ssids(self):
        self.assertEqual(wifi.parse_nmcli(r"*:Plant\:A\\west:80:WPA2"), [["*", "Plant:A\\west", "80", "WPA2"]])

    def test_status_uses_connected_wifi_not_ethernet_and_removes_cidr(self):
        def command(*args, **kwargs):
            if args[-2:] == ("device", "status"):
                return "eth0:ethernet:connected\nwlan1:wifi:disconnected\nwlan0:wifi:connected\n"
            if args[-3:] == ("device", "show", "wlan0"):
                return f"GENERAL.STATE:100 (connected)\nGENERAL.CON-UUID:{PROFILE}\nIP4.ADDRESS[1]:192.168.43.12/24\nIP4.ADDRESS[2]:127.0.0.1/8\n"
            return "connection.type:802-11-wireless\nconnection.autoconnect:yes\n802-11-wireless.ssid:Phone\n802-11-wireless.mode:infrastructure\n"
        with patch.object(wifi, "run_nmcli", side_effect=command):
            state = wifi.wifi_status()
        self.assertEqual(state, {"interface":"wlan0", "connected":True, "ssid":"Phone", "profile_id":PROFILE, "autoconnect":True, "ip_addresses":["192.168.43.12"]})

    def test_disconnected_wifi_does_not_show_stale_ip(self):
        with patch.object(wifi, "wifi_interface", return_value="wlan0"), patch.object(wifi, "run_nmcli", return_value="GENERAL.STATE:30 (disconnected)\nIP4.ADDRESS[1]:192.168.1.5/24\n"):
            state = wifi.wifi_status()
        self.assertFalse(state["connected"])
        self.assertEqual(state["ip_addresses"], [])

    def test_scan_deduplicates_and_keeps_connected_ap(self):
        output="*:Phone:50:WPA2\n:Phone:90:WPA2\n:Other:80:--\n::0:WPA2\n"
        with patch.object(wifi, "wifi_interface", return_value="wlan0"), patch.object(wifi, "run_nmcli", return_value=output):
            result = wifi.wifi_networks()
        self.assertEqual(len(result["networks"]), 2)
        self.assertEqual(result["networks"][0]["ssid"], "Phone")
        self.assertTrue(result["networks"][0]["connected"])

    def test_connection_validation_rejects_control_characters_and_bad_types(self):
        for payload in ([], {"ssid":""}, {"ssid":"a" * 33}, {"ssid":"Phone\nquit"},
                        {"ssid":"Phone", "password":"secret\nquit"}, {"ssid":"Phone", "password":"123"},
                        {"ssid":"Phone", "autoconnect":"yes"}, {"ssid":"Phone", "hidden":1}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                wifi.validate_connection(payload)

    def connect(self, command, **payload):
        with patch.object(wifi, "wifi_interface", return_value="wlan0"), patch.object(wifi, "wifi_networks", return_value={"networks":[{"ssid":"Phone", "security":"WPA2"}]}), patch.object(wifi.uuid, "uuid4", return_value=NEW_PROFILE), patch.object(wifi, "run_nmcli", side_effect=command) as runner:
            result = wifi.connect_wifi({"ssid":"Phone", "password":"secret123", "autoconnect":True, **payload})
        return result, runner

    def test_password_uses_stdin_and_autoconnect_is_persisted(self):
        result, runner = self.connect(lambda *args, **kwargs: "")
        self.assertEqual(result["profile_id"], NEW_PROFILE)
        for call in runner.call_args_list:
            self.assertNotIn("secret123", " ".join(call.args))
        edit = next(call for call in runner.call_args_list if call.args[:2] == ("connection", "edit"))
        self.assertEqual(edit.kwargs["input_text"], "set wifi-sec.psk secret123\nsave persistent\nquit\n")
        self.assertEqual(runner.call_args_list[-1].args[-2:], ("connection.autoconnect", "yes"))

    def test_new_connection_can_disable_autoconnect(self):
        _, runner = self.connect(lambda *args, **kwargs: "", autoconnect=False)
        self.assertEqual(runner.call_args_list[-1].args[-2:], ("connection.autoconnect", "no"))

    def test_saved_network_reconnects_without_creating_a_profile(self):
        def command(*args, **kwargs):
            return f"{PROFILE}:802-11-wireless\n" if args[-2:] == ("connection", "show") else ""
        with patch.object(wifi, "profile_details", return_value={"id":PROFILE, "ssid":"Phone", "autoconnect":True}):
            result, runner = self.connect(command, password="")
        self.assertEqual(result["profile_id"], PROFILE)
        self.assertFalse(any(call.args[:2] in (("connection", "add"), ("connection", "edit")) for call in runner.call_args_list))

    def test_failed_activation_deletes_only_new_profile(self):
        calls = []
        def command(*args, **kwargs):
            calls.append(args)
            if args[:2] == ("connection", "up"):
                raise wifi.WifiError("Wrong password", 502)
            return ""
        with self.assertRaises(wifi.WifiError):
            self.connect(command)
        self.assertEqual(calls[-1], ("connection", "delete", "uuid", NEW_PROFILE))

    def test_failed_autoconnect_update_does_not_delete_working_connection(self):
        calls = []
        def command(*args, **kwargs):
            calls.append(args)
            if args[:2] == ("connection", "modify"):
                raise wifi.WifiError("Permission denied", 403)
            return ""
        with self.assertRaises(wifi.WifiError):
            self.connect(command)
        self.assertFalse(any(args[:2] == ("connection", "delete") for args in calls))

    def test_hidden_network_does_not_require_scan(self):
        _, runner = self.connect(lambda *args, **kwargs: "", hidden=True)
        add = next(call for call in runner.call_args_list if call.args[:2] == ("connection", "add"))
        self.assertIn("802-11-wireless.hidden", add.args)

    def test_enterprise_new_network_is_rejected(self):
        with patch.object(wifi, "wifi_interface", return_value="wlan0"), patch.object(wifi, "run_nmcli", return_value=""), patch.object(wifi, "wifi_networks", return_value={"networks":[{"ssid":"Office", "security":"WPA2 802.1X"}]}), self.assertRaises(ValueError):
            wifi.connect_wifi({"ssid":"Office", "password":"secret123"})

    def test_autoconnect_only_modifies_current_profile(self):
        state={"connected":True, "profile_id":PROFILE, "ssid":"Phone"}
        with patch.object(wifi, "wifi_status", return_value=state), patch.object(wifi, "saved_networks", return_value=[]), patch.object(wifi, "run_nmcli") as runner:
            with self.assertRaises(ValueError):
                wifi.set_autoconnect({"profile_id":NEW_PROFILE, "autoconnect":True})
            runner.assert_not_called()
            wifi.set_autoconnect({"profile_id":PROFILE, "autoconnect":False})
            runner.assert_called_once_with("connection", "modify", "uuid", PROFILE, "connection.autoconnect", "no")

    def test_old_duplicate_profile_cannot_override_autoconnect_off(self):
        previous={"id":PROFILE, "ssid":"Phone", "autoconnect":True}
        with patch.object(wifi, "saved_networks", return_value=[previous]):
            _, runner = self.connect(lambda *args, **kwargs: "", autoconnect=False)
        runner.assert_any_call("connection", "modify", "uuid", NEW_PROFILE, "connection.autoconnect", "no")
        runner.assert_any_call("connection", "modify", "uuid", PROFILE, "connection.autoconnect", "no")

    def test_concurrent_changes_are_rejected(self):
        wifi._change_lock.acquire()
        try:
            with self.assertRaises(wifi.WifiError) as error:
                wifi.connect_wifi({"ssid":"Phone", "password":"secret123"})
            self.assertEqual(error.exception.status, 409)
        finally:
            wifi._change_lock.release()

    def test_subprocess_errors_never_expose_passwords(self):
        response=SimpleNamespace(returncode=1, stdout="secret123", stderr="not authorized")
        with patch.object(wifi.sys, "platform", "linux"), patch.object(wifi.shutil, "which", return_value="/usr/bin/nmcli"), patch.object(wifi.subprocess, "run", return_value=response) as runner:
            with self.assertRaises(wifi.WifiError) as error:
                wifi.run_nmcli("connection", "edit", "uuid", PROFILE, input_text="secret123")
        self.assertNotIn("secret123", str(error.exception))
        self.assertEqual(error.exception.status, 403)
        self.assertNotIn("secret123", runner.call_args.args[0])
        self.assertNotIn("shell", runner.call_args.kwargs)

    def test_timeout_and_unsupported_platform_have_useful_errors(self):
        with patch.object(wifi.sys, "platform", "win32"), self.assertRaises(wifi.WifiError):
            wifi.wifi_status()
        with patch.object(wifi.sys, "platform", "linux"), patch.object(wifi.shutil, "which", return_value="/usr/bin/nmcli"), patch.object(wifi.subprocess, "run", side_effect=subprocess.TimeoutExpired("nmcli", 10)), self.assertRaises(wifi.WifiError) as error:
            wifi.run_nmcli("device", "status")
        self.assertEqual(error.exception.status, 504)


class WifiApiTests(unittest.TestCase):
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

    def test_wifi_endpoints_require_admin(self):
        with self.client.session_transaction() as session:
            session.update(user_id=self.operator_id, username="operator")
        for path, method in (("status","get"), ("networks","get"), ("connect","post"), ("autoconnect","put")):
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(f"/api/wifi/{path}", json={}).status_code, 403)

    def test_status_returns_wifi_ip_and_tab_exists(self):
        with patch.object(wifi, "wifi_status", return_value={"interface":"wlan0", "connected":True, "ssid":"Phone", "ip_addresses":["192.168.43.12"]}):
            response=self.client.get("/api/wifi/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["ip_addresses"], ["192.168.43.12"])
        self.assertIn(b'id="wifi-tab"', self.client.get("/device-settings").data)

    def test_connect_and_autoconnect_calls_are_validated(self):
        payload={"ssid":"Phone", "password":"secret123", "autoconnect":True}
        with patch.object(wifi, "connect_wifi", return_value={"ssid":"Phone", "profile_id":PROFILE}) as connect:
            response=self.client.post("/api/wifi/connect", json=payload)
        self.assertEqual(response.status_code, 200)
        connect.assert_called_once_with(payload)
        self.assertNotIn("secret123", response.get_data(as_text=True))
        with patch.object(wifi, "set_autoconnect", return_value={"autoconnect":False}):
            self.assertEqual(self.client.put("/api/wifi/autoconnect", json={"profile_id":PROFILE, "autoconnect":False}).status_code, 200)

    def test_bad_payload_origin_and_os_errors(self):
        self.assertEqual(self.client.post("/api/wifi/connect", data="bad").status_code, 400)
        self.assertEqual(self.client.post("/api/wifi/connect", json={}, headers={"Origin":"https://another-site.test"}).status_code, 403)
        with patch.object(wifi, "wifi_status", side_effect=wifi.WifiError("NetworkManager unavailable")):
            self.assertEqual(self.client.get("/api/wifi/status").status_code, 503)
        with patch.object(wifi, "connect_wifi", side_effect=ValueError("Enter password")):
            self.assertEqual(self.client.post("/api/wifi/connect", json={}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
