import json
import time
import threading
import socket
import queue

import serial
from pymavlink import mavutil

# =======================
# AYARLAR
# =======================
SIK_PORT = "/dev/ttyUSB0"
SIK_BAUD = 115200

PIX_PORT = "/dev/serial0"
PIX_BAUD = 115200

# Telemetry rate limit (Hz)
ATT_HZ     = 10
ALT_HZ     = 5
BAT_HZ     = 2
GPS_HZ     = 2
MODE_HZ    = 2
PAYLOAD_HZ = 5

# Servo kanalları (AUX portuna göre ayarla)
LEFT_SERVO_FIELD  = "servo14_raw"
RIGHT_SERVO_FIELD = "servo13_raw"

# Heartbeat timeout (saniye)
HB_TIMEOUT  = 5.0
# Pixhawk bağlantısında heartbeat bekleme süresi (saniye)
PIX_HB_WAIT = 10
# Vision sisteminden gelen UDP drop komutları için port
VISION_UDP_PORT = 14555

# Servo kanalları (vision drop için)
DROP_SERVO = {
    "first":  14,   # kırmızı hedef → AUX14
    "second": 13,   # mavi hedef → AUX13
}
SERVO_DROP_PWM = 1900
SERVO_HOLD_PWM = 1100

# =======================
# ArduCopter uçuş modları
# https://ardupilot.org/copter/docs/flight-modes.html
# =======================
ARDUCOPTER_MODES = {
    "STABILIZE":    0,
    "ACRO":         1,
    "ALT_HOLD":     2,
    "AUTO":         3,
    "GUIDED":       4,
    "LOITER":       5,
    "RTL":          6,
    "CIRCLE":       7,
    "LAND":         9,
    "DRIFT":        11,
    "SPORT":        13,
    "FLIP":         14,
    "AUTOTUNE":     15,
    "POSHOLD":      16,
    "BRAKE":        17,
    "THROW":        18,
    "AVOID_ADSB":   19,
    "GUIDED_NOGPS": 20,
    "SMART_RTL":    21,
    "FLOWHOLD":     22,
    "FOLLOW":       23,
    "ZIGZAG":       24,
    "SYSTEMID":     25,
    "AUTOROTATE":   26,
    "AUTO_RTL":     27,
}

ARDUCOPTER_MODE_NAMES = {v: k for k, v in ARDUCOPTER_MODES.items()}


class RPiBridge:
    def __init__(self):
        self.ser      = None
        self.ser_lock = threading.Lock()
        self.last_pc_ping = time.time()

        self.master   = None
        self.pix_lock = threading.Lock()

        self.hb_lock   = threading.Lock()
        self.hb_ok     = False
        self.last_hb_t = 0.0

        self._current_mode_id = -1

        self.mission_active = False
        self.mission_name   = None
        self.mission_start  = None

        self._last        = {}
        self._last_sent_t = {}
        self._stop        = False

        # MISSION mesajlarını upload thread'ine yönlendirmek için queue
        self._mission_q    = queue.Queue()
        self._upload_active = False

        # Vision UDP socket
        self._udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_sock.bind(("127.0.0.1", VISION_UDP_PORT))
        self._udp_sock.settimeout(1.0)

    # =======================
    # UI send helpers
    # =======================
    def send_ui(self, obj: dict):
        line = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self.ser_lock:
            if not self.ser:
                return
            try:
                self.ser.write(line)
            except Exception:
                self.ser = None

    def _send_if_changed(self, key: str, payload: dict, min_interval_s: float = 0.0):
        now    = time.time()
        last_t = self._last_sent_t.get(key, 0.0)
        if min_interval_s > 0 and (now - last_t) < min_interval_s:
            return
        if self._last.get(key) == payload:
            return
        self._last[key]        = payload
        self._last_sent_t[key] = now
        self.send_ui(payload)

    # =======================
    # Start
    # =======================
    def start(self):
        threading.Thread(target=self._ui_open_loop,    daemon=True).start()
        threading.Thread(target=self._pix_open_loop,   daemon=True).start()
        threading.Thread(target=self.ui_rx_loop,       daemon=True).start()
        threading.Thread(target=self.ui_watchdog_loop, daemon=True).start()
        threading.Thread(target=self.hb_watchdog_loop, daemon=True).start()
        threading.Thread(target=self.timer_loop,       daemon=True).start()
        threading.Thread(target=self.pix_rx_loop,      daemon=True).start()
        threading.Thread(target=self.vision_udp_loop,  daemon=True).start()

        while not self._stop:
            time.sleep(1)

    # =======================
    # Open loops (reconnect)
    # =======================
    def _ui_open_loop(self):
        while not self._stop:
            with self.ser_lock:
                ser_none = self.ser is None
            if ser_none:
                try:
                    new_ser = serial.Serial(SIK_PORT, SIK_BAUD, timeout=0.1)
                    with self.ser_lock:
                        self.ser = new_ser
                    self.send_ui({"type": "status", "msg": "sik_connected"})
                except Exception:
                    time.sleep(1.0)
            else:
                time.sleep(2.0)

    def _pix_open_loop(self):
        """
        FIX 1: Bağlantı açıldıktan sonra heartbeat bekleniyor.
        wait_heartbeat() target_system ve target_component'i otomatik set eder.
        Heartbeat gelmezse master=None bırakılır, UI'a bildirilir.
        """
        while not self._stop:
            with self.pix_lock:
                master_none = self.master is None
            if master_none:
                new_master = None
                try:
                    new_master = mavutil.mavlink_connection(PIX_PORT, baud=PIX_BAUD)

                    # Heartbeat bekle — bu çağrı target_system/component'i otomatik set eder.
                    # Timeout olursa pymavlink exception fırlatır (None dönmez),
                    # dolayısıyla hata yönetimi aşağıdaki except bloğunda yapılır.
                    new_master.wait_heartbeat(timeout=PIX_HB_WAIT)

                    # Veri akışını başlat
                    new_master.mav.request_data_stream_send(
                        new_master.target_system,
                        new_master.target_component,
                        mavutil.mavlink.MAV_DATA_STREAM_ALL,
                        4,  # 4 Hz
                        1,  # start
                    )

                    with self.pix_lock:
                        self.master = new_master

                    self.send_ui({
                        "type":   "status",
                        "msg":    "pix_link_open",
                        "sysid":  new_master.target_system,
                        "compid": new_master.target_component,
                    })

                except Exception as e:
                    # wait_heartbeat() timeout da buraya düşer
                    err_str = str(e).lower()
                    msg_key = "pix_no_hb" if "timeout" in err_str else f"pix_open_err:{e}"
                    self.send_ui({"type": "status", "msg": msg_key})
                    try:
                        if new_master is not None:
                            new_master.close()
                    except Exception:
                        pass
                    time.sleep(2.0)
            else:
                time.sleep(2.0)

    # =======================
    # UI RX
    # FIX 2: ser_lock ile lokal referans alınıyor — yarış durumu giderildi
    # =======================
    def ui_rx_loop(self):
        buf = b""
        while not self._stop:
            with self.ser_lock:
                ser = self.ser
            if not ser:
                time.sleep(0.2)
                continue
            try:
                chunk = ser.read(256)
            except Exception:
                with self.ser_lock:
                    if self.ser is ser:
                        self.ser = None
                time.sleep(0.5)
                continue

            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", errors="ignore"))
                    self.handle_ui_msg(msg)
                except Exception:
                    pass

    def handle_ui_msg(self, msg: dict):
        t = msg.get("type")

        if t == "ping":
            self.last_pc_ping = time.time()
            return

        if t == "cmd":
            name = msg.get("name", "")
            if name == "hold":
                self.set_mode("LOITER")
                self.mission_active = False
            elif name == "rtl":
                self.set_mode("RTL")
                self.mission_active = False
            elif name == "land":
                self.set_mode("LAND")
                self.mission_active = False
            elif name == "disarm":
                self.disarm()
            elif name == "kill":
                self.kill_motors()
            return

        if t == "set_mode":
            mode_name = msg.get("mode", "").upper()
            if mode_name in ARDUCOPTER_MODES:
                self.set_mode(mode_name)
            else:
                self.send_ui({"type": "status", "msg": f"unknown_mode:{mode_name}"})
            return

        if t == "mission":
            name = msg.get("name")
            if name == "task1":
                self.arm()
                self.mission_active = True
                self.mission_name = name
                self.mission_start = time.time()
                self.set_mode("AUTO")
                self.send_ui({"type": "status", "msg": "task1_started_auto"})
                return
            elif name == "task2":
                self.arm()
                self.mission_active = True
                self.mission_name = name
                self.mission_start = time.time()
                self.set_mode("AUTO")
                self.send_ui({"type": "status", "msg": "task2_started_auto"})
                return

        if t == "wp_upload":
            waypoints = msg.get("waypoints", [])
            n = len(waypoints)
            mission = msg.get("mission", "task1")  # default

            if n < 2:
                self.send_ui({"type": "status", "msg": "wp_upload_need_2plus"})
                return

            self.send_ui({"type": "status", "msg": f"{mission}_wp_upload:{n}"})
            threading.Thread(target=self._upload_waypoints, args=(waypoints,), daemon=True).start()
            return

    # =======================
    # UI watchdog
    # =======================
    def ui_watchdog_loop(self):
        while not self._stop:
            diff   = time.time() - self.last_pc_ping
            status = "BAĞLI" if diff < 2 else ("ZAYIF" if diff < 5 else "KOPUK")
            self._send_if_changed(
                "pc_link",
                {"type": "pc_link", "status": status},
                min_interval_s=0.5,
            )
            time.sleep(0.5)

    # =======================
    # Heartbeat watchdog
    # FIX 3: hb_lock ile thread-safe erişim
    # =======================
    def hb_watchdog_loop(self):
        while not self._stop:
            with self.hb_lock:
                ok = self.hb_ok
                t  = self.last_hb_t
            if ok and (time.time() - t) > HB_TIMEOUT:
                with self.hb_lock:
                    self.hb_ok = False
                self.send_ui({"type": "status", "msg": "pix_hb_lost"})
            time.sleep(1.0)

    # =======================
    # Timer loop
    # =======================
    def timer_loop(self):
        while not self._stop:
            if self.mission_active and self.mission_start:
                sec = int(time.time() - self.mission_start)
                self._send_if_changed(
                    "timer", {"type": "timer", "sec": sec}, min_interval_s=0.2
                )
            time.sleep(0.2)

    # =======================
    # Vision UDP listener
    # Vision'dan gelen mesajlar:
    #   {"type":"vision_guided"}                        → hedef görüldü, GUIDED moda geç (hover)
    #   {"type":"vision_lost"}                          → hedef kayboldu, AUTO moda dön
    #   {"type":"vision_drop","which":"first"/"second"} → aim tamam, drop at + AUTO'ya dön
    # =======================
    def vision_udp_loop(self):
        while not self._stop:
            try:
                data, _ = self._udp_sock.recvfrom(1024)
                msg = json.loads(data.decode("utf-8"))
                t = msg.get("type")

                if t == "vision_guided":
                    # Hedef görüldü → GUIDED moda geç, drone hover yapar
                    self.set_mode("GUIDED")
                    self.send_ui({"type": "status", "msg": "vision:target_found→GUIDED"})
                    print("[Bridge] vision_guided → GUIDED mod")

                elif t == "vision_lost":
                    # Hedef kayboldu → AUTO moda dön, lawnmower devam eder
                    self.set_mode("AUTO")
                    self.send_ui({"type": "status", "msg": "vision:target_lost→AUTO"})
                    print("[Bridge] vision_lost → AUTO mod")

                elif t == "vision_drop":
                    which = msg.get("which")   # "first" veya "second"
                    if which in DROP_SERVO:
                        self._fire_drop_servo(which)
                    # Drop sonrası AUTO'ya dön, taramaya devam
                    self.set_mode("AUTO")
                    self.send_ui({"type": "status", "msg": f"vision:drop_done:{which}→AUTO"})
                    print(f"[Bridge] drop:{which} → AUTO mod")

            except socket.timeout:
                continue
            except Exception as e:
                print(f"[UDP] hata: {e}")

    def _fire_drop_servo(self, which: str):
        with self.pix_lock:
            master = self.master
        if not master:
            self.send_ui({"type": "status", "msg": "drop_err:pix_not_connected"})
            return

        ch = DROP_SERVO[which]

        def set_servo(pwm):
            master.mav.command_long_send(
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
                0, ch, pwm, 0, 0, 0, 0, 0
            )

        try:
            set_servo(SERVO_DROP_PWM)
            time.sleep(0.6)
            set_servo(SERVO_HOLD_PWM)
            self.send_ui({"type": "status", "msg": f"drop_ok:{which}:ch{ch}"})
        except Exception as e:
            self.send_ui({"type": "status", "msg": f"drop_err:{e}"})

    # =======================
    # Pixhawk kontrol
    # =======================
    def set_mode(self, mode_name: str):
        with self.pix_lock:
            master = self.master
        if not master:
            self.send_ui({"type": "status", "msg": "pix_not_connected"})
            return

        mode_id = ARDUCOPTER_MODES.get(mode_name.upper())
        if mode_id is None:
            self.send_ui({"type": "status", "msg": f"unknown_mode:{mode_name}"})
            return

        try:
            master.mav.set_mode_send(
                master.target_system,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                mode_id,
            )
            self._send_if_changed(
                "mode_cmd",
                {"type": "mode_cmd", "mode": mode_name, "mode_id": mode_id},
                min_interval_s=0.1,
            )
        except Exception as e:
            self.send_ui({"type": "status", "msg": f"set_mode_err:{e}"})

    def disarm(self):
        with self.pix_lock:
            master = self.master
        if not master:
            self.send_ui({"type": "status", "msg": "pix_not_connected"})
            return
        try:
            master.arducopter_disarm()
            self.send_ui({"type": "status", "msg": "disarmed"})
        except Exception as e:
            self.send_ui({"type": "status", "msg": f"disarm_err:{e}"})

    def arm(self):
        with self.pix_lock:
            master = self.master
        if not master:
            self.send_ui({"type": "status", "msg": "pix_not_connected"})
            return False

        try:
            master.arducopter_arm()
            self.send_ui({"type": "status", "msg": "armed_cmd_sent"})
            return True
        except Exception as e:
            self.send_ui({"type": "status", "msg": f"arm_err:{e}"})
            return False

    def kill_motors(self):
        """
        ACİL DURDURMA — MAV_CMD_COMPONENT_ARM_DISARM force (magic: 21196)
        Pixhawk 6x + ArduCopter 4.x: MOT_SAFE_DISARM=0, DISARM_DELAY=0 olmalı.
        """
        with self.pix_lock:
            master = self.master
        if not master:
            self.send_ui({"type": "status", "msg": "pix_not_connected"})
            return
        try:
            master.mav.command_long_send(
                master.target_system,
                master.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0,
                0,      # disarm
                21196,  # force magic
                0, 0, 0, 0, 0,
            )
            self.send_ui({"type": "status", "msg": "KILL_SENT"})
        except Exception as e:
            self.send_ui({"type": "status", "msg": f"kill_err:{e}"})

    def _upload_waypoints(self, waypoints: list):
        """
        MAVLink mission upload protokolü.
        pix_rx_loop MISSION mesajlarını _mission_q'ya koyar,
        biz buradan okuruz — telemetri akmaya devam eder.
        """
        with self.pix_lock:
            master = self.master
        if not master:
            self.send_ui({"type": "status", "msg": "wp_err:pix_not_connected"})
            return

        # Queue'yu temizle, upload modunu aç
        while not self._mission_q.empty():
            try: self._mission_q.get_nowait()
            except: pass
        self._upload_active = True

        try:
            count = len(waypoints) + 3  # +3: home, arm, disarm
            self.send_ui({"type": "status", "msg": f"wp_upload_start:{len(waypoints)}"})

            master.mav.mission_count_send(
                master.target_system,
                master.target_component,
                count,
                mavutil.mavlink.MAV_MISSION_TYPE_MISSION,
            )

            sent    = set()
            timeout = time.time() + 15.0

            while time.time() < timeout:
                try:
                    req = self._mission_q.get(timeout=3.0)
                except Exception:
                    continue  # 3s içinde mesaj gelmedi, devam

                rtype = req.get_type()

                if rtype == "MISSION_ACK":
                    if req.type == mavutil.mavlink.MAV_MISSION_ACCEPTED:
                        self.send_ui({"type": "status", "msg": "wp_upload_ok"})
                    else:
                        self.send_ui({"type": "status", "msg": f"wp_upload_nack:{req.type}"})
                    return

                seq = req.seq
                if seq in sent:
                    continue
                sent.add(seq)

                last_seq = count - 1

                if seq == 0:
                    # HOME placeholder (ArduPilot standard)
                    master.mav.mission_item_int_send(
                        master.target_system,
                        master.target_component,
                        0,
                        mavutil.mavlink.MAV_FRAME_GLOBAL,
                        mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                        0, 1,
                        0, 0, 0, 0,
                        0, 0, 0,
                        mavutil.mavlink.MAV_MISSION_TYPE_MISSION,
                    )


                elif seq == 1:
                    takeoff_alt = float(waypoints[0].get("alt", 20.0))
                    master.mav.mission_item_int_send(
                        master.target_system,
                        master.target_component,
                        1,
                        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                        0, 1,
                        0, 0, 0, 0,
                        0, 0, takeoff_alt,  # ✅ lat/lon 0: "current", alt: target
                        mavutil.mavlink.MAV_MISSION_TYPE_MISSION,
                    )

                elif seq == last_seq:
                    # LAND: son WP'nin üstüne indir
                    lat = int(waypoints[-1]["lat"] * 1e7)
                    lon = int(waypoints[-1]["lon"] * 1e7)

                    master.mav.mission_item_int_send(
                        master.target_system,
                        master.target_component,
                        last_seq,
                        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                        mavutil.mavlink.MAV_CMD_NAV_LAND,
                        0, 1,
                        0, 0, 0, 0,
                        lat, lon, 0.0,
                        mavutil.mavlink.MAV_MISSION_TYPE_MISSION,
                    )

                else:
                    # USER WP: seq=2 -> waypoints[0], seq=3 -> waypoints[1], ...
                    wp = waypoints[seq - 2]
                    lat = int(wp["lat"] * 1e7)
                    lon = int(wp["lon"] * 1e7)
                    alt = float(wp.get("alt", 20.0))

                    master.mav.mission_item_int_send(
                        master.target_system,
                        master.target_component,
                        seq,
                        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                        mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
                        0, 1,
                        0, 2.0, 0,
                        float("nan"),
                        lat, lon, alt,
                        mavutil.mavlink.MAV_MISSION_TYPE_MISSION,
                    )

            self.send_ui({"type": "status", "msg": "wp_upload_timeout"})

        except Exception as e:
            self.send_ui({"type": "status", "msg": f"wp_upload_err:{e}"})

        finally:
            self._upload_active = False  # her durumda kapat

    # =======================
    # Pixhawk RX -> UI telemetri
    # =======================
    def pix_rx_loop(self):
        last_att     = 0.0
        last_spd     = 0.0
        last_alt     = 0.0
        last_bat     = 0.0
        last_gps     = 0.0
        last_mode    = 0.0
        last_payload = 0.0

        while not self._stop:
            with self.pix_lock:
                master = self.master
            if not master:
                time.sleep(0.2)
                continue

            try:
                msg = master.recv_match(blocking=True, timeout=1.0)
            except Exception:
                # FIX 4: close() garantili çağrılıyor, sonra None'a set et
                with self.pix_lock:
                    if self.master is master:
                        try:
                            master.close()
                        except Exception:
                            pass
                        self.master = None
                time.sleep(0.2)
                continue

            if not msg:
                continue

            mtype = msg.get_type()
            now   = time.time()

            # Upload aktifse MISSION mesajlarını queue'ya yönlendir, normal işleme yapma
            if self._upload_active and mtype in (
                "MISSION_REQUEST_INT", "MISSION_REQUEST", "MISSION_ACK"
            ):
                self._mission_q.put(msg)
                continue

            # ---------- HEARTBEAT ----------
            if mtype == "HEARTBEAT" and msg.get_srcSystem() != 255:
                with self.hb_lock:   # FIX 3
                    self.hb_ok     = True
                    self.last_hb_t = now

                if (now - last_mode) >= (1.0 / MODE_HZ):
                    mode_id   = getattr(msg, "custom_mode", -1)
                    mode_name = ARDUCOPTER_MODE_NAMES.get(mode_id, f"MODE_{mode_id}")
                    armed     = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)

                    self._current_mode_id = mode_id
                    self._send_if_changed(
                        "mode",
                        {
                            "type":    "mode",
                            "mode":    mode_name,
                            "mode_id": mode_id,
                            "armed":   armed,
                        },
                        min_interval_s=1.0 / MODE_HZ,
                    )
                    last_mode = now
                continue

            # ---------- BATTERY ----------
            if mtype == "SYS_STATUS" and (now - last_bat) >= (1.0 / BAT_HZ):
                voltage_v = round(getattr(msg, "voltage_battery", 0) / 1000.0, 2)
                current_a = round(getattr(msg, "current_battery", 0) / 100.0,  2)
                rem       = int(getattr(msg, "battery_remaining", -1))
                self._send_if_changed(
                    "battery",
                    {
                        "type":      "battery",
                        "rem":       rem,
                        "current_a": current_a,
                        "voltage_v": voltage_v,
                    },
                    min_interval_s=1.0 / BAT_HZ,
                )
                last_bat = now
                continue

            # ---------- VFR_HUD — hız ----------
            if mtype == "VFR_HUD" and (now - last_spd) >= (1.0 / ALT_HZ):
                spd = round(getattr(msg, "groundspeed", 0.0), 2)
                self._send_if_changed(
                    "speed",
                    {"type": "speed", "mps": spd},
                    min_interval_s=1.0 / ALT_HZ,
                )
                last_spd = now
                continue

            # ---------- GLOBAL_POSITION_INT — irtifa + konum ----------
            if mtype == "GLOBAL_POSITION_INT" and (now - last_alt) >= (1.0 / ALT_HZ):
                rel_m = round(getattr(msg, "relative_alt", 0) / 1000.0, 2)
                lat   = round(getattr(msg, "lat", 0) / 1e7, 7)
                lon   = round(getattr(msg, "lon", 0) / 1e7, 7)
                self._send_if_changed(
                    "alt",
                    {"type": "alt", "rel_m": rel_m},
                    min_interval_s=1.0 / ALT_HZ,
                )
                self._send_if_changed(
                    "pos",
                    {"type": "pos", "lat": lat, "lon": lon, "rel_m": rel_m},
                    min_interval_s=1.0 / GPS_HZ,
                )
                last_alt = now
                continue

            if mtype == "ATTITUDE" and (now - last_att) >= (1.0 / ATT_HZ):
                pitch = round(getattr(msg, "pitch", 0.0) * 57.2958, 1)
                roll = round(getattr(msg, "roll", 0.0) * 57.2958, 1)
                yaw = round(getattr(msg, "yaw", 0.0) * 57.2958, 1)

                # -180..+180 → 0..360 (heading gibi göstermek için)
                if yaw < 0:
                    yaw += 360.0

                self._send_if_changed(
                    "att",
                    {"type": "att", "pitch": pitch, "roll": roll, "yaw": yaw},
                    min_interval_s=1.0 / ATT_HZ,
                )
                last_att = now
                continue

            # ---------- GPS ----------
            if mtype == "GPS_RAW_INT" and (now - last_gps) >= (1.0 / GPS_HZ):
                fix_type = int(getattr(msg, "fix_type", 0))
                sats     = int(getattr(msg, "satellites_visible", 0))
                self._send_if_changed(
                    "gps",
                    {
                        "type":     "gps",
                        "fix":      fix_type >= 3,
                        "fix_type": fix_type,
                        "sats":     sats,
                    },
                    min_interval_s=1.0 / GPS_HZ,
                )
                last_gps = now
                continue

            # ---------- SERVO / PAYLOAD ----------
            if mtype == "SERVO_OUTPUT_RAW" and (now - last_payload) >= (1.0 / PAYLOAD_HZ):
                p1_pwm = int(getattr(msg, LEFT_SERVO_FIELD,  0))
                p2_pwm = int(getattr(msg, RIGHT_SERVO_FIELD, 0))
                self._send_if_changed(
                    "payload",
                    {
                        "type":   "payload",
                        "p1_raw": p1_pwm,
                        "p2_raw": p2_pwm,
                    },
                    min_interval_s=1.0 / PAYLOAD_HZ,
                )
                last_payload = now
                continue


if __name__ == "__main__":
    RPiBridge().start()
