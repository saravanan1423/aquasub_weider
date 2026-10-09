import unittest
from unittest.mock import patch

from communication_setting.serial_monitor import SerialMonitor


class ScaleConnectionTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.clock = patch("communication_setting.serial_monitor.time.monotonic", side_effect=lambda: self.now)
        self.clock.start()
        self.monitor = SerialMonitor()
        self.monitor.config = {"start_marker": "$", "end_marker": "]", "start_address": 1,
                               "end_address": 5, "frame_timeout": 1, "frame_gap": .25}
        self.monitor.connected = True

    def tearDown(self):
        self.clock.stop()

    def test_open_serial_port_without_values_is_not_a_connected_scale(self):
        state = self.monitor.snapshot()
        self.assertTrue(state["connected"])
        self.assertFalse(state["scale_connected"])
        self.assertEqual(state["weight"], "")
        self.monitor._append(b"\r\nnoise\r\n")
        self.assertFalse(self.monitor.snapshot()["scale_connected"])

    def test_numeric_value_and_zero_connect_scale(self):
        for weight in ("00020", "00000", "0", "-2.5", "+2.5"):
            with self.subTest(weight=weight):
                self.monitor._append(f"${weight}]\r\n".encode())
                state = self.monitor.snapshot()
                self.assertTrue(state["scale_connected"])
                self.assertEqual(state["weight"], weight)

    def test_invalid_selected_value_disconnects_without_reusing_old_value(self):
        self.monitor._append(b"$00020]\r\n")
        self.monitor._append(b"$ERROR]\r\n")
        self.assertFalse(self.monitor.snapshot()["scale_connected"])
        self.assertEqual(self.monitor.snapshot()["weight"], "")
        self.monitor._append(b"$00000]\r\n")
        self.assertTrue(self.monitor.snapshot()["scale_connected"])

    def test_silence_and_noise_expire_weight_and_fresh_reading_recovers(self):
        self.monitor._append(b"$00020]\r\n")
        self.now += 1.9
        self.monitor._append(b"noise\r\n")
        self.assertTrue(self.monitor.snapshot()["scale_connected"])
        self.now += .2
        self.assertFalse(self.monitor.snapshot()["scale_connected"])
        self.assertEqual(self.monitor.snapshot()["weight"], "")
        self.monitor._append(b"$00020]\r\n")
        self.assertTrue(self.monitor.snapshot()["scale_connected"])

    def test_split_value_and_configured_positions_are_supported(self):
        self.monitor.config.update(start_address=5, end_address=10)
        self.monitor._append(b"$%05[000")
        self.assertFalse(self.monitor.snapshot()["scale_connected"])
        self.monitor._append(b"020]")
        self.assertEqual(self.monitor.snapshot()["weight"], "000020")

    def test_multicharacter_markers_and_latest_value_are_supported(self):
        self.monitor.config.update(start_marker="ST:", end_marker="kg", end_address=10)
        self.monitor._append(b"ST:12.5kg ST:0kg")
        self.assertTrue(self.monitor.snapshot()["scale_connected"])
        self.assertEqual(self.monitor.snapshot()["weight"], "0")

    def test_slow_frame_setting_allows_its_configured_framing_delay(self):
        self.monitor.config.update(frame_timeout=5)
        self.monitor._append(b"$00020]")
        self.now += 4
        self.assertTrue(self.monitor.snapshot()["scale_connected"])
        self.now += 2
        self.assertFalse(self.monitor.snapshot()["scale_connected"])

    def test_stability_requires_received_equal_readings_for_two_seconds(self):
        self.monitor._append(b"$00020]")
        self.now += 1
        self.monitor._append(b"$20.00]")
        self.now += .9
        self.monitor._append(b"$00020]")
        self.assertFalse(self.monitor.snapshot()["weight_stable"])
        self.now += .1
        self.monitor._append(b"$00020]")
        self.assertTrue(self.monitor.snapshot()["weight_stable"])
        self.monitor._append(b"$00021]")
        self.assertFalse(self.monitor.snapshot()["weight_stable"])

    def test_single_reading_does_not_become_stable_during_silence(self):
        self.monitor.config.update(frame_timeout=5)
        self.monitor._append(b"$00020]")
        self.now += 3
        self.assertTrue(self.monitor.snapshot()["scale_connected"])
        self.assertFalse(self.monitor.snapshot()["weight_stable"])

    def test_invalid_reading_or_silence_restarts_stability(self):
        self.monitor._append(b"$00020]")
        for _ in range(2):
            self.now += 1
            self.monitor._append(b"$00020]")
        self.assertTrue(self.monitor.snapshot()["weight_stable"])
        self.monitor._append(b"$ERROR]")
        self.monitor._append(b"$00020]")
        self.assertFalse(self.monitor.snapshot()["weight_stable"])
        for _ in range(2):
            self.now += 1
            self.monitor._append(b"$00020]")
        self.assertTrue(self.monitor.snapshot()["weight_stable"])
        self.now += 2.1
        self.assertFalse(self.monitor.snapshot()["weight_stable"])
        self.monitor._append(b"$00020]")
        self.assertFalse(self.monitor.snapshot()["weight_stable"])

    def test_disconnected_port_never_exposes_old_weight(self):
        self.monitor._append(b"$00020]")
        self.monitor.connected = False
        self.assertFalse(self.monitor.snapshot()["scale_connected"])
        self.assertEqual(self.monitor.snapshot()["weight"], "")
        self.monitor.stop()
        self.monitor.connected = True
        self.assertFalse(self.monitor.snapshot()["scale_connected"])


if __name__ == "__main__":
    unittest.main()
