import threading
import time
import re
from collections import deque
from datetime import datetime
from decimal import Decimal

import serial

BYTE_SIZES = {5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS, 8: serial.EIGHTBITS}
PARITIES = {"none": serial.PARITY_NONE, "even": serial.PARITY_EVEN, "odd": serial.PARITY_ODD, "mark": serial.PARITY_MARK, "space": serial.PARITY_SPACE}
STOP_BITS = {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE, "2": serial.STOPBITS_TWO}


def printable_text(data):
    return data.decode("ascii", errors="backslashreplace").rstrip("\r\n")


def take_complete_frames(buffer):
    frames = []
    while buffer:
        positions = [position for position in (buffer.find(b"\r"), buffer.find(b"\n")) if position >= 0]
        if not positions: break
        delimiter = min(positions)
        if delimiter == len(buffer) - 1 and buffer[delimiter] == 13: break
        end = delimiter + 1
        while end < len(buffer) and buffer[end] in (10, 13): end += 1
        frames.append(bytes(buffer[:end])); del buffer[:end]
    return frames


class SerialMonitor:
    def __init__(self):
        self._lock = threading.Lock(); self._stop = threading.Event(); self._thread = None; self._serial = None
        self._frames = deque(maxlen=1000); self._next_id = 1; self.connected = False; self.connecting = False; self.error = ""; self.config = {}
        self._weight_buffer = ""; self._weight = ""; self._last_valid_weight_at = None
        self._stable_since = None

    def start(self, config):
        self.stop()
        with self._lock:
            self.config = config; self.error = ""; self.connecting = True; self._frames.clear(); self._next_id = 1; self._stop = threading.Event()
            self._weight_buffer = ""; self._weight = ""; self._last_valid_weight_at = None
            self._stable_since = None
        self._thread = threading.Thread(target=self._read_loop, daemon=True); self._thread.start()

    def stop(self):
        self._stop.set()
        if self._serial is not None:
            try: self._serial.cancel_read()
            except (AttributeError, serial.SerialException): pass
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread(): self._thread.join(timeout=1.5)
        with self._lock:
            self.connected = False; self.connecting = False
            self._weight_buffer = ""; self._weight = ""; self._last_valid_weight_at = None
            self._stable_since = None
        self._serial = None

    def _read_weight(self, frame):
        start_marker = self.config.get("start_marker", "")
        end_marker = self.config.get("end_marker", "")
        if not start_marker or not end_marker:
            return
        stream = self._weight_buffer + frame.decode("latin1")
        while True:
            end = stream.find(end_marker)
            if end < 0:
                break
            start = stream.rfind(start_marker, 0, end)
            if start >= 0:
                selected = stream[start + len(start_marker):end]
                weight = selected[self.config.get("start_address", 1) - 1:self.config.get("end_address", len(selected))].strip()
                if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", weight):
                    now = time.monotonic()
                    if (self._last_valid_weight_at is None
                            or now - self._last_valid_weight_at >= self._reading_timeout()
                            or not self._weight or Decimal(weight) != Decimal(self._weight)):
                        self._stable_since = now
                    self._weight = weight
                    self._last_valid_weight_at = now
                else:
                    self._weight = ""
                    self._last_valid_weight_at = None
                    self._stable_since = None
            stream = stream[end + len(end_marker):]
        self._weight_buffer = stream[-16384:]

    def _append(self, frame):
        with self._lock:
            self._read_weight(frame)
            self._frames.append({"id": self._next_id, "time": datetime.now().astimezone().isoformat(timespec="milliseconds"), "text": printable_text(frame), "hex": frame.hex(" ").upper(), "bytes": list(frame), "length": len(frame)})
            self._next_id += 1

    def _read_loop(self):
        buffer = bytearray(); last_byte_at = None; frame_started_at = None; config = self.config
        try:
            self._serial = serial.Serial(port=config["port"], baudrate=config["baud_rate"], bytesize=BYTE_SIZES[config["data_bits"]], parity=PARITIES[config["parity"]], stopbits=STOP_BITS[config["stop_bits"]], timeout=0.02)
            with self._lock: self.connected = True; self.connecting = False
            while not self._stop.is_set():
                waiting = self._serial.in_waiting; chunk = self._serial.read(waiting if waiting else 1)
                if chunk:
                    buffer.extend(chunk); last_byte_at = time.monotonic(); frame_started_at = frame_started_at or last_byte_at
                    for frame in take_complete_frames(buffer): self._append(frame)
                    if not buffer: frame_started_at = None
                    elif last_byte_at - frame_started_at >= config["frame_timeout"]:
                        self._append(bytes(buffer)); buffer.clear(); last_byte_at = frame_started_at = None
                    continue
                now = time.monotonic()
                if buffer and ((last_byte_at and now-last_byte_at >= config["frame_gap"]) or (frame_started_at and now-frame_started_at >= config["frame_timeout"])):
                    self._append(bytes(buffer)); buffer.clear(); last_byte_at = frame_started_at = None
        except (serial.SerialException, OSError, ValueError, KeyError) as error:
            with self._lock:
                if not self._stop.is_set():
                    message = str(error)
                    if "Permission denied" in message: message += " — add your Linux user to the dialout group, then log out and back in"
                    elif "No such file" in message: message += " — reconnect the device and refresh the serial-port list"
                    self.error = message
        finally:
            if buffer: self._append(bytes(buffer))
            if self._serial is not None and self._serial.is_open: self._serial.close()
            with self._lock: self.connected = False; self.connecting = False
            self._serial = None

    def _reading_timeout(self):
        return max(2.0, self.config.get("frame_timeout", 1) + self.config.get("frame_gap", .25))

    def snapshot(self, after=0):
        with self._lock:
            timeout = self._reading_timeout()
            scale_connected = (self.connected and self._last_valid_weight_at is not None
                               and time.monotonic() - self._last_valid_weight_at < timeout)
            # Require received readings spanning two seconds, not a single old sample.
            weight_stable = (scale_connected and self._stable_since is not None
                             and self._last_valid_weight_at - self._stable_since >= 2.0)
            return {"connected": self.connected, "connecting": self.connecting,
                    "weight_stable": weight_stable,
                    "scale_connected": scale_connected, "weight": self._weight if scale_connected else "",
                    "last_frame_id": self._next_id - 1,
                    "error": self.error, "config": self.config,
                    "frames": [frame for frame in self._frames if frame["id"] > after]}

    def clear(self):
        with self._lock: self._frames.clear()


monitor = SerialMonitor()
