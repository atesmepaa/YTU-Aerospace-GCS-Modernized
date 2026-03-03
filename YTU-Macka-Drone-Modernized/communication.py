"""
communication.py
~~~~~~~~~~~~~~~~
SiK telemetri radyosu üzerinden JSON haberleşmesini yönetir.
"""

import json
import threading
import time

import serial

from config import SIK_PORT, SIK_BAUD, DEBUG_SIK_RX, DEBUG_JSON_FAIL


class SiKLink:
    def __init__(self, on_message, on_link_status):
        self._on_message      = on_message
        self._on_link_status  = on_link_status
        self._sik: serial.Serial | None = None
        self._lock            = threading.Lock()
        self.last_rx_time     = 0.0
        self.rx_count         = 0
        self._last_ui_status  = "BEKLENİYOR"

    def start(self):
        self._try_open()
        threading.Thread(target=self._rx_loop,             daemon=True).start()
        threading.Thread(target=self._ping_loop,           daemon=True).start()
        threading.Thread(target=self._reconnect_loop,      daemon=True).start()
        threading.Thread(target=self._link_indicator_loop, daemon=True).start()

    def send(self, obj: dict):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            if not self._sik or not self._sik.is_open:
                return
            try:
                self._sik.write(data)
            except Exception:
                self._close_locked()

    def _try_open(self):
        try:
            s = serial.Serial(SIK_PORT, SIK_BAUD, timeout=0.1)
            with self._lock:
                self._sik = s
            print(f"[SiK] {SIK_PORT}@{SIK_BAUD} açıldı")
        except Exception as e:
            print(f"[SiK] Port açılamadı: {e}")

    def _close_locked(self):
        try: self._sik.close()
        except Exception: pass
        self._sik = None

    def _rx_loop(self):
        buf = b""
        while True:
            with self._lock:
                sik = self._sik
            if not sik:
                time.sleep(0.2); continue
            try:
                chunk = sik.read(256)
                if chunk:
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if not line: continue
                        if DEBUG_SIK_RX:
                            print(f"[SiK RX] {line[:120]}")
                        try:
                            msg = json.loads(line.decode("utf-8", errors="ignore"))
                            self.last_rx_time = time.time()
                            self.rx_count += 1
                            self._on_message(msg)
                        except Exception as e:
                            if DEBUG_JSON_FAIL:
                                print(f"[SiK] JSON fail: {e} | {line[:80]}")
            except Exception:
                with self._lock:
                    if self._sik is sik:
                        self._close_locked()
                time.sleep(0.2)

    def _ping_loop(self):
        while True:
            self.send({"type": "ping", "t": time.time()})
            time.sleep(0.5)

    def _reconnect_loop(self):
        while True:
            with self._lock:
                is_none = self._sik is None
            if is_none:
                self._on_link_status("YENİDEN BAĞLANILIYOR...")
                self._try_open()
            time.sleep(2.0)

    def _link_indicator_loop(self):
        while True:
            now  = time.time()
            diff = now - self.last_rx_time
            if self.last_rx_time == 0.0: status = "BEKLENİYOR"
            elif diff < 15:              status = "BAĞLI"
            elif diff <= 30:             status = "ZAYIF"
            else:                        status = "KOPUK"
            if status != self._last_ui_status:
                self._last_ui_status = status
                self._on_link_status(status)
            time.sleep(0.5)