"""
main.py - YTU Macka GCS v3
Dark tactical aesthetic: siyah paneller, turuncu vurgular, Consolas font.
"""

import json
import sys
import threading
import urllib.request
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

from config import ARDU_MODE_DISPLAY, MOUSE_IMU_ENABLED, RPI_STREAM_URL, THEME
from communication import SiKLink
from mission_logic import (
    generate_task1_figure8_waypoints,
    generate_task2_scan_waypoints,
    waypoints_to_payload,
    pts_to_payload,
)
from ui_components import CameraWidget, IMUWidget, MapWidget, PayloadWidget, TelemetryGrid

T = THEME

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")


class DroneApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Ground Control Station")
        self.attributes("-fullscreen", False)
        self.configure(fg_color=T["bg_root"])
        if sys.platform.startswith("win"):
            import ctypes
            # Benzersiz bir ID ver (şirket.proje.uygulama gibi)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "ytu.macka.gcs.v3"
            )
            self.iconbitmap("logo.ico")


        self._awaiting_wp_ok = False
        self._pending_mission_after_upload = None
        self._last_shown_alt = None
        self._last_shown_spd = None
        self._ALT_THRESHOLD  = 0.3
        self._SPD_THRESHOLD  = 0.2
        self._mouse_pitch  = 0.0
        self._mouse_roll   = 0.0
        self._mouse_drag   = False
        self._mouse_last_x = 0
        self._mouse_last_y = 0

        self._setup_ui()

        self._sik = SiKLink(
            on_message     = lambda m: self.after(0, lambda msg=m: self._handle(msg)),
            on_link_status = lambda s: self.after(0, lambda st=s: self._set_link(st)),
        )
        self._sik.start()

        if MOUSE_IMU_ENABLED:
            self._start_mouse_imu()

    # ------------------------------------------------------------------ send
    def _send(self, obj):
        self._sik.send(obj)

    # ------------------------------------------------------------------ msg handler
    def _handle(self, msg: dict):
        t = msg.get("type")

        if t == "battery":
            self._lbl_bat.configure(text=f"{msg.get('voltage_v', 0):.1f} V")
            self._lbl_rem.configure(text=f"%{msg.get('rem', 0)}")
            self._lbl_cur.configure(text=f"{msg.get('current_a', 0):.1f} A")

        elif t == "mode":
            display = ARDU_MODE_DISPLAY.get(msg.get("mode", ""), msg.get("mode", ""))
            armed   = msg.get("armed", False)
            self._lbl_mode.configure(text=display)
            self._lbl_arm.configure(
                text="ARM" if armed else "DISARM",
                text_color=T["danger"] if armed else T["text_secondary"],
            )

        elif t == "alt":
            v = abs(float(msg.get("rel_m", 0)))
            if self._last_shown_alt is None or abs(v - self._last_shown_alt) >= self._ALT_THRESHOLD:
                self._last_shown_alt = v
                self._lbl_alt.configure(text=f"{v:.1f} m")

        elif t == "speed":
            v = float(msg.get("mps", 0))
            if self._last_shown_spd is None or abs(v - self._last_shown_spd) >= self._SPD_THRESHOLD:
                self._last_shown_spd = v
                self._lbl_spd.configure(text=f"{v:.1f} m/s")

        elif t == "gps":
            fix  = msg.get("fix", False)
            ft   = msg.get("fix_type", 0)
            sats = msg.get("sats", 0)
            self._lbl_gps.configure(
                text=f"FIX {ft}D  {sats}*" if fix else f"NO FIX  {sats}*",
                text_color=T["ok"] if fix else T["warn"],
            )

        elif t == "att":
            yaw = float(msg.get("yaw", 0))
            self._lbl_yaw.configure(text=f"{yaw:.0f} deg")
            if not MOUSE_IMU_ENABLED:
                self._imu.update(float(msg.get("pitch", 0)), float(msg.get("roll", 0)))

        elif t == "pos":
            lat, lon = msg.get("lat"), msg.get("lon")
            if lat is not None:
                self._map.update_drone_pos(float(lat), float(lon))

        elif t == "payload":
            THR = 1500
            self._payload.set_payload1(msg.get("p1_raw", 0) > THR)
            self._payload.set_payload2(msg.get("p2_raw", 0) > THR)

        elif t == "timer":
            s = int(msg.get("sec", 0))
            self._lbl_timer.configure(text=f"{s//60:02d}:{s%60:02d}")

        elif t == "pc_link":
            self._set_link(msg.get("status", "KOPUK"))

        elif t == "status":
            sm = msg.get("msg", "")
            self._lbl_status.configure(text=sm.upper())
            if sm == "wp_upload_ok":
                self._awaiting_wp_ok = False
                p = self._pending_mission_after_upload
                self._pending_mission_after_upload = None
                if p in ("task1", "task2"):
                    self._send({"type": "mission", "name": p})
                    self._lbl_status.configure(text=f"{p.upper()} BASLADI")
            elif sm == "wp_clear_ok":
                self._lbl_status.configure(text="WP TEMIZLENDI")

    def _set_link(self, s: str):
        color_map = {
            "BAGLI": T["ok"],
            "ZAYIF": T["warn"],
            "KOPUK": T["danger"],
            "YENIDEN BAGLANILIYOR...": T["warn"],
        }
        self._lbl_link.configure(text=s, text_color=color_map.get(s, T["text_secondary"]))

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(0, weight=1)

        # Sol panel
        left = ctk.CTkFrame(self, fg_color=T["bg_panel"], corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        self._build_header(left)

        center = ctk.CTkFrame(left, fg_color="transparent")
        center.grid(row=1, column=0, sticky="nsew", padx=8, pady=(4, 8))
        center.grid_columnconfigure(0, weight=1)
        center.grid_columnconfigure(1, weight=1)
        center.grid_rowconfigure(0, weight=1)

        self._cam = CameraWidget(center)
        self._cam.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        self._map = MapWidget(center, on_upload_request=self._on_upload)
        self._map.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        # Sag panel
        right = ctk.CTkFrame(self, fg_color=T["bg_card"], corner_radius=0,
                             border_width=1, border_color=T["border"])
        right.grid(row=0, column=1, sticky="nsew")
        right.configure(width=220)
        right.grid_propagate(False)
        right.grid_rowconfigure(10, weight=1)

        self._build_right_panel(right)

    def _build_header(self, parent):
        hdr = ctk.CTkFrame(parent, fg_color=T["bg_card"], corner_radius=0, height=150,
                           border_width=1, border_color=T["border"])
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)

        # Logo
        logo_frame = ctk.CTkFrame(hdr, fg_color="transparent", width=80)
        logo_frame.grid(row=0, column=0, sticky="ns", padx=(10, 0), pady=6)
        logo_frame.grid_propagate(False)
        try:
            img = Image.open("logo.png")
        except FileNotFoundError:
            img = Image.new("RGBA", (70, 70), (255, 107, 26, 255))
        self._logo_ref = ctk.CTkImage(light_image=img, dark_image=img, size=(70, 60))
        ctk.CTkLabel(logo_frame, image=self._logo_ref, text="").pack(expand=True)

        # Telemetri izgara
        tg = TelemetryGrid(hdr)
        tg.grid(row=0, column=1, sticky="nsew", padx=8, pady=6)

        self._lbl_mode  = tg.add("MOD",      "--")
        self._lbl_arm   = tg.add("DURUM",    "DISARM")
        self._lbl_alt   = tg.add("IRTIFA",   "0.0 m")
        self._lbl_spd   = tg.add("HIZ",      "0.0 m/s")
        self._lbl_bat   = tg.add("VOLTAJ",   "0.0 V")
        self._lbl_rem   = tg.add("BATARYA",  "%0")
        self._lbl_cur   = tg.add("AKIM",     "0.0 A")
        self._lbl_gps   = tg.add("GPS",      "BEKLENIYOR")
        self._lbl_yaw   = tg.add("YAW",      "0 deg")
        self._lbl_timer = tg.add("GOREV",    "00:00")

        # IMU + link + status
        right_hdr = ctk.CTkFrame(hdr, fg_color="transparent")
        right_hdr.grid(row=0, column=2, sticky="ns", padx=(0, 10), pady=6)

        self._imu = IMUWidget(right_hdr)
        self._imu.pack()

        link_row = ctk.CTkFrame(right_hdr, fg_color="transparent")
        link_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(link_row, text="LINK:", font=(T["font_family"], 9),
                     text_color=T["text_secondary"]).pack(side="left")
        self._lbl_link = ctk.CTkLabel(link_row, text="BEKLENIYOR",
                                      font=(T["font_family"], 9, "bold"),
                                      text_color=T["text_secondary"])
        self._lbl_link.pack(side="left", padx=4)

        self._lbl_status = ctk.CTkLabel(right_hdr, text="HAZIR",
                                        font=(T["font_family"], 9),
                                        text_color=T["text_secondary"],
                                        wraplength=120)
        self._lbl_status.pack(pady=(2, 0))

    def _build_right_panel(self, parent):
        ctk.CTkLabel(parent, text="YTU MACKA",
                     font=(T["font_family"], 13, "bold"),
                     text_color=T["accent"]).pack(pady=(14, 0))
        ctk.CTkLabel(parent, text="AEROSPACE GCS",
                     font=(T["font_family"], 9),
                     text_color=T["text_secondary"]).pack(pady=(0, 10))

        ctk.CTkFrame(parent, height=1, fg_color=T["border"]).pack(fill="x", padx=10)

        self._payload = PayloadWidget(parent)
        self._payload.pack(fill="x", padx=8, pady=8)

        ctk.CTkFrame(parent, height=1, fg_color=T["border"]).pack(fill="x", padx=10)

        btn_defs = [
            ("HOLD",     "#1e2a1e",          T["ok"],     self.cmd_hold),
            ("RTL",      "#1e1e2a",          "#4488ff",   self.cmd_rtl),
            ("LAND",     "#1e1e2a",          "#4488ff",   self.cmd_land),
            ("WP RESET", "#2a1a1a",          T["danger"], self.cmd_wp_clear),
            ("GOREV 1",  T["bg_card_dark"],  T["accent"], self.cmd_task1),
            ("GOREV 2",  T["bg_card_dark"],  T["accent"], self.cmd_task2),
        ]
        for text, bg, accent, cmd in btn_defs:
            ctk.CTkButton(
                parent, text=text,
                fg_color=bg, hover_color=accent,
                text_color=T["text_primary"],
                border_width=1, border_color=accent,
                font=(T["font_family"], 11, "bold"),
                height=36, width=200, corner_radius=4,
                command=cmd,
            ).pack(pady=4, padx=10)

        ctk.CTkFrame(parent, fg_color="transparent").pack(expand=True)

        ctk.CTkButton(
            parent, text="!  ACIL DURDUR",
            fg_color="#3a0000", hover_color="#6a0000",
            text_color=T["danger"],
            font=(T["font_family"], 12, "bold"),
            height=42, width=200, corner_radius=4,
            border_width=1, border_color=T["danger"],
            command=self.cmd_kill,
        ).pack(pady=(0, 12), padx=10)

    # ------------------------------------------------------------------ mouse imu
    def _start_mouse_imu(self):
        self.bind("<ButtonPress-1>",   lambda e: self._mp(e))
        self.bind("<B1-Motion>",       lambda e: self._mm(e))
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_mouse_drag", False))
        self.bind("<ButtonPress-3>",   lambda e: (
            setattr(self, "_mouse_pitch", 0.0),
            setattr(self, "_mouse_roll",  0.0),
            self._imu.update(0.0, 0.0),
        ))

    def _mp(self, e):
        self._mouse_drag = True
        self._mouse_last_x, self._mouse_last_y = e.x_root, e.y_root

    def _mm(self, e):
        if not self._mouse_drag:
            return
        dx = e.x_root - self._mouse_last_x
        dy = e.y_root - self._mouse_last_y
        self._mouse_last_x, self._mouse_last_y = e.x_root, e.y_root
        self._mouse_roll  = max(-90, min(90, self._mouse_roll  + dx * 0.225))
        self._mouse_pitch = max(-90, min(90, self._mouse_pitch - dy * 0.225))
        self._imu.update(self._mouse_pitch, self._mouse_roll)

    # ------------------------------------------------------------------ upload
    def _on_upload(self, waypoints, mode, spacing_m, alt):
        if not waypoints:
            messagebox.showwarning("Uyari", "Waypoint yok!"); return
        if len(waypoints) == 1:
            messagebox.showwarning("Uyari", "En az 2 WP gerekli!"); return
        if self._awaiting_wp_ok:
            self._lbl_status.configure(text="YUKLEME DEVAM EDIYOR"); return

        if len(waypoints) == 2 and mode == "TASK1":
            pts = generate_task1_figure8_waypoints(waypoints, n_per_circle=8)
            if len(pts) < 3:
                messagebox.showwarning("Task1", "Figure-8 uretilemedi."); return
            wp = pts_to_payload(pts, alt)
            self._awaiting_wp_ok = True
            self._pending_mission_after_upload = "task1"
            self._send({"type": "wp_upload", "waypoints": wp, "mission": "task1"})
            self._lbl_status.configure(text=f"TASK1  {len(wp)} WP")

        elif len(waypoints) == 2 and mode == "TASK2":
            pts = generate_task2_scan_waypoints(waypoints, spacing_m=spacing_m)
            if len(pts) < 2:
                messagebox.showwarning("Task2", "Tarama uretilemedi."); return
            wp = pts_to_payload(pts, alt)
            self._awaiting_wp_ok = True
            self._pending_mission_after_upload = "task2"
            self._send({"type": "wp_upload", "waypoints": wp, "mission": "task2"})
            self._lbl_status.configure(text=f"TASK2  {len(wp)} WP")

        else:
            wp = waypoints_to_payload(waypoints, alt)
            self._awaiting_wp_ok = True
            self._pending_mission_after_upload = "task1"
            self._send({"type": "wp_upload", "waypoints": wp, "mission": "task1"})
            self._lbl_status.configure(text=f"TASK1  {len(wp)} NOKTA")

    # ------------------------------------------------------------------ commands
    def cmd_hold(self):
        self._send({"type": "cmd", "name": "hold"})

    def cmd_rtl(self):
        self._send({"type": "cmd", "name": "rtl"})

    def cmd_land(self):
        self._send({"type": "cmd", "name": "land"})

    def cmd_wp_clear(self):
        if not messagebox.askyesno("WP Sil", "Pixhawk WP'leri silinecek!"): return
        self._send({"type": "wp_clear"})
        self._lbl_status.configure(text="WP SILME GONDERILDI")

    def cmd_task1(self):
        if not messagebox.askyesno("Task1", "Birinci gorevi baslat?"): return
        wps = self._map.waypoints
        if len(wps) < 2:
            messagebox.showwarning("Task1", "En az 2 WP sec."); return
        if self._awaiting_wp_ok:
            self._lbl_status.configure(text="YUKLEME DEVAM EDIYOR"); return
        self._on_upload(wps, self._map.get_mode(), 3.0, 10.0)

    def cmd_task2(self):
        if not messagebox.askyesno("Task2", "Ikinci gorevi baslat?"): return
        if self._map.get_mode() != "TASK2":
            messagebox.showwarning("Task2", "Harita modunu TASK2 yap."); return
        wps = self._map.waypoints
        if len(wps) != 2:
            messagebox.showwarning("Task2", "Tam 2 WP sec."); return
        if self._awaiting_wp_ok:
            self._lbl_status.configure(text="YUKLEME DEVAM EDIYOR"); return
        self._on_upload(wps, "TASK2", 6.0, 20.0)

        def _vision():
            try:
                req = urllib.request.Request(
                    f"{RPI_STREAM_URL}/start", data=b"", method="POST")
                with urllib.request.urlopen(req, timeout=3) as r:
                    print(f"[Vision] {json.loads(r.read().decode())}")
            except Exception as e:
                print(f"[Vision] {e}")
        threading.Thread(target=_vision, daemon=True).start()

    def cmd_kill(self):
        if messagebox.askyesno("ACIL", "MOTORLAR KAPATILACAK! Emin misin?"):
            self._send({"type": "cmd", "name": "kill"})


if __name__ == "__main__":
    app = DroneApp()
    app.mainloop()