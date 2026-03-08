"""
main.py - YTU Macka GCS v4
Teknofest geliştirmeleri:
  - Görev state machine (HAZIR → WP YÜKLENİYOR → GÖREV → RTL → TAMAMLANDI)
  - Figure-8 ×2: 3 WP (Direk1, Direk2, Pist) — resme uygun CW/CCW mantığı
  - Görev tamamlanınca otomatik RTL
  - Kill butonu: 2 sn basılı tut
  - Scan önizleme çizgileri haritada
  - drop_target mesajı → GUIDED + fly-to
"""

import json
import sys
import threading
import time
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

# ── Görev fazları ──────────────────────────────────────────────────────────────
PHASE_IDLE      = "HAZIR"
PHASE_UPLOADING = "WP YÜKLENİYOR"
PHASE_RUNNING   = "GÖREV DEVAM EDİYOR"
PHASE_DROP_WAIT = "DROP BEKLENİYOR"
PHASE_DROP_DONE = "DROP YAPILDI"
PHASE_RTL       = "RTL"
PHASE_DONE      = "TAMAMLANDI"

PHASE_COLORS = {
    PHASE_IDLE:      "#888888",
    PHASE_UPLOADING: "#ffab00",
    PHASE_RUNNING:   "#00e676",
    PHASE_DROP_WAIT: "#ff8c42",
    PHASE_DROP_DONE: "#00e676",
    PHASE_RTL:       "#4488ff",
    PHASE_DONE:      "#00e676",
}


class DroneApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Ground Control Station — YTÜ Maçka Aerospace")
        self.attributes("-fullscreen", False)
        self.configure(fg_color=T["bg_root"])
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "ytu.macka.gcs.v4"
            )
            try:
                self.iconbitmap("logo.ico")
            except Exception:
                pass

        # ── Durum ──────────────────────────────────────────────────────────────
        self._awaiting_wp_ok              = False
        self._pending_mission_after_upload = None
        self._last_shown_alt              = None
        self._last_shown_spd              = None
        self._ALT_THRESHOLD               = 0.3
        self._SPD_THRESHOLD               = 0.2
        self._mouse_pitch                 = 0.0
        self._mouse_roll                  = 0.0
        self._mouse_drag                  = False
        self._mouse_last_x                = 0
        self._mouse_last_y                = 0
        self._active_task                 = None   # "task1" | "task2" | None
        self._kill_press_time             = None   # basılı tut mekanizması
        self._kill_job                    = None

        self._setup_ui()
        self._set_phase(PHASE_IDLE)

        self._sik = SiKLink(
            on_message     = lambda m: self.after(0, lambda msg=m: self._handle(msg)),
            on_link_status = lambda s: self.after(0, lambda st=s: self._set_link(st)),
        )
        self._sik.start()

        if MOUSE_IMU_ENABLED:
            self._start_mouse_imu()

    # ──────────────────────────────────────────── faz yönetimi
    def _set_phase(self, phase: str, extra: str = ""):
        color = PHASE_COLORS.get(phase, T["text_secondary"])
        text  = phase + (f"  {extra}" if extra else "")
        self._lbl_phase.configure(text=text, text_color=color)
        # Görev süresi: yeni görev başlayınca timer label'ı sıfırla
        if phase == PHASE_RUNNING:
            self._lbl_timer.configure(text="00:00")

    # ──────────────────────────────────────────── send
    def _send(self, obj):
        self._sik.send(obj)

    # ──────────────────────────────────────────── mesaj handler
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
            if sm == "wp_upload_ok":
                self._awaiting_wp_ok = False
                p = self._pending_mission_after_upload
                self._pending_mission_after_upload = None
                if p in ("task1", "task2"):
                    self._send({"type": "mission", "name": p})
                    self._active_task = p
                    self._set_phase(PHASE_RUNNING, p.upper())

            elif sm == "wp_clear_ok":
                self._set_phase(PHASE_IDLE, "WP TEMİZLENDİ")

            elif sm == "mission_complete":
                # Görev bitti → otomatik RTL
                if self._active_task == "task1":
                    self._send({"type": "cmd", "name": "rtl"})
                    self._set_phase(PHASE_RTL, "OTO")
                    self._active_task = None
                elif self._active_task == "task2":
                    # Task2: önce drop bekleniyor
                    self._set_phase(PHASE_DROP_WAIT)

            elif sm == "drop_done":
                self._set_phase(PHASE_DROP_DONE)
                self.after(1500, lambda: (
                    self._send({"type": "cmd", "name": "rtl"}),
                    self._set_phase(PHASE_RTL, "OTO"),
                ))
                self._active_task = None

            elif sm == "rtl_complete":
                self._set_phase(PHASE_DONE)
                self._active_task = None

        elif t == "drop_target":
            # RPi görüntü işlemeden hedef koordinat geldi
            lat = msg.get("lat")
            lon = msg.get("lon")
            alt = msg.get("alt", 5.0)
            if lat is not None and lon is not None:
                self._set_phase(PHASE_DROP_WAIT, f"{lat:.5f},{lon:.5f}")
                # GUIDED moda geç ve hedefe git
                self._send({"type": "cmd", "name": "guided",
                            "lat": lat, "lon": lon, "alt": alt})
                # Haritada hedef göster
                self._map.set_drop_target(float(lat), float(lon))

    def _set_link(self, s: str):
        color_map = {
            "BAĞLI":               T["ok"],
            "ZAYIF":               T["warn"],
            "KOPUK":               T["danger"],
            "YENİDEN BAĞLANILIYOR...": T["warn"],
        }
        self._lbl_link.configure(text=s, text_color=color_map.get(s, T["text_secondary"]))

    # ──────────────────────────────────────────── UI kurulumu
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

        # Sağ panel
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

        # Telemetri ızgarası
        tg = TelemetryGrid(hdr)
        tg.grid(row=0, column=1, sticky="nsew", padx=8, pady=6)

        self._lbl_mode  = tg.add("MOD",      "--")
        self._lbl_arm   = tg.add("DURUM",    "DISARM")
        self._lbl_alt   = tg.add("İRTİFA",   "0.0 m")
        self._lbl_spd   = tg.add("HIZ",      "0.0 m/s")
        self._lbl_bat   = tg.add("VOLTAJ",   "0.0 V")
        self._lbl_rem   = tg.add("BATARYA",  "%0")
        self._lbl_cur   = tg.add("AKIM",     "0.0 A")
        self._lbl_gps   = tg.add("GPS",      "BEKLENİYOR")
        self._lbl_yaw   = tg.add("YAW",      "0 deg")
        self._lbl_timer = tg.add("GÖREV",    "00:00")

        # IMU + link + faz durumu
        right_hdr = ctk.CTkFrame(hdr, fg_color="transparent")
        right_hdr.grid(row=0, column=2, sticky="ns", padx=(0, 10), pady=6)

        self._imu = IMUWidget(right_hdr)
        self._imu.pack()

        link_row = ctk.CTkFrame(right_hdr, fg_color="transparent")
        link_row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(link_row, text="LINK:", font=(T["font_family"], 9),
                     text_color=T["text_secondary"]).pack(side="left")
        self._lbl_link = ctk.CTkLabel(link_row, text="BEKLENİYOR",
                                      font=(T["font_family"], 9, "bold"),
                                      text_color=T["text_secondary"])
        self._lbl_link.pack(side="left", padx=4)

        # Görev fazı etiketi (ana yenilik)
        self._lbl_phase = ctk.CTkLabel(
            right_hdr, text=PHASE_IDLE,
            font=(T["font_family"], 10, "bold"),
            text_color=T["text_secondary"],
            wraplength=130,
        )
        self._lbl_phase.pack(pady=(4, 0))

    def _build_right_panel(self, parent):
        ctk.CTkLabel(parent, text="YTÜ MAÇKA",
                     font=(T["font_family"], 13, "bold"),
                     text_color=T["accent"]).pack(pady=(14, 0))
        ctk.CTkLabel(parent, text="AEROSPACE GCS  v4",
                     font=(T["font_family"], 9),
                     text_color=T["text_secondary"]).pack(pady=(0, 10))

        ctk.CTkFrame(parent, height=1, fg_color=T["border"]).pack(fill="x", padx=10)

        self._payload = PayloadWidget(parent)
        self._payload.pack(fill="x", padx=8, pady=8)

        ctk.CTkFrame(parent, height=1, fg_color=T["border"]).pack(fill="x", padx=10)

        btn_defs = [
            ("HOLD",     "#1e2a1e",         T["ok"],     self.cmd_hold),
            ("RTL",      "#1e1e2a",         "#4488ff",   self.cmd_rtl),
            ("LAND",     "#1e1e2a",         "#4488ff",   self.cmd_land),
            ("WP RESET", "#2a1a1a",         T["danger"], self.cmd_wp_clear),
            ("GÖREV 1",  T["bg_card_dark"], T["accent"], self.cmd_task1),
            ("GÖREV 2",  T["bg_card_dark"], T["accent"], self.cmd_task2),
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

        # Kill — basılı tut mekanizması
        self._kill_btn = ctk.CTkButton(
            parent, text="⚠  ACİL DURDUR",
            fg_color="#3a0000", hover_color="#6a0000",
            text_color=T["danger"],
            font=(T["font_family"], 12, "bold"),
            height=42, width=200, corner_radius=4,
            border_width=1, border_color=T["danger"],
        )
        self._kill_btn.pack(pady=(0, 12), padx=10)
        self._kill_btn.bind("<ButtonPress-1>",   self._kill_press)
        self._kill_btn.bind("<ButtonRelease-1>", self._kill_release)

    # ──────────────────────────────────────────── kill basılı tut
    def _kill_press(self, _event=None):
        self._kill_press_time = time.time()
        self._kill_btn.configure(text="⚠  BASILI TUT... (2s)")
        self._kill_job = self.after(2000, self._kill_confirm)

    def _kill_release(self, _event=None):
        if self._kill_job:
            self.after_cancel(self._kill_job)
            self._kill_job = None
        self._kill_btn.configure(text="⚠  ACİL DURDUR")

    def _kill_confirm(self):
        self._kill_job = None
        self._kill_btn.configure(text="⚠  ACİL DURDUR")
        self._send({"type": "cmd", "name": "kill"})
        self._set_phase(PHASE_IDLE, "KİLL")

    # ──────────────────────────────────────────── mouse imu
    def _start_mouse_imu(self):
        self.bind("<ButtonPress-1>",   self._mp)
        self.bind("<B1-Motion>",       self._mm)
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

    # ──────────────────────────────────────────── upload
    def _on_upload(self, waypoints, mode, spacing_m, alt):
        if not waypoints:
            messagebox.showwarning("Uyarı", "Waypoint yok!"); return
        if self._awaiting_wp_ok:
            self._set_phase(PHASE_UPLOADING, "DEVAM EDİYOR"); return

        if mode == "TASK1":
            if len(waypoints) != 3:
                messagebox.showwarning(
                    "Task1", "Tam 3 WP gerekli:\n  WP1=Direk1  WP2=Direk2  WP3=Pist"
                ); return
            pts = generate_task1_figure8_waypoints(waypoints, n_per_circle=12, n_loops=2)
            if len(pts) < 3:
                messagebox.showwarning("Task1", "Figure-8 üretilemedi."); return
            wp = pts_to_payload(pts, alt)
            self._awaiting_wp_ok = True
            self._pending_mission_after_upload = "task1"
            self._send({"type": "wp_upload", "waypoints": wp, "mission": "task1"})
            self._set_phase(PHASE_UPLOADING, f"{len(wp)} WP")

        elif mode == "TASK2":
            if len(waypoints) != 4:
                messagebox.showwarning("Task2", "Tam 4 WP gerekli:\n  WP1-WP2 = Alana gidiş\n  WP3-WP4 = Tarama alanı köşeleri"); return
            pts = generate_task2_scan_waypoints(waypoints, spacing_m=spacing_m)
            if len(pts) < 2:
                messagebox.showwarning("Task2", "Tarama üretilemedi."); return
            wp = pts_to_payload(pts, alt)
            self._awaiting_wp_ok = True
            self._pending_mission_after_upload = "task2"
            self._send({"type": "wp_upload", "waypoints": wp, "mission": "task2"})
            self._set_phase(PHASE_UPLOADING, f"{len(wp)} WP")

        else:
            wp = waypoints_to_payload(waypoints, alt)
            self._awaiting_wp_ok = True
            self._pending_mission_after_upload = "task1"
            self._send({"type": "wp_upload", "waypoints": wp, "mission": "task1"})
            self._set_phase(PHASE_UPLOADING, f"{len(wp)} NOKTA")

    # ──────────────────────────────────────────── komutlar
    def cmd_hold(self):
        self._send({"type": "cmd", "name": "hold"})
        self._set_phase(PHASE_IDLE, "LOITER")

    def cmd_rtl(self):
        self._send({"type": "cmd", "name": "rtl"})
        self._set_phase(PHASE_RTL, "MANUEL")

    def cmd_land(self):
        self._send({"type": "cmd", "name": "land"})
        self._set_phase(PHASE_IDLE, "İNİŞ")

    def cmd_wp_clear(self):
        if not messagebox.askyesno("WP Sil", "Pixhawk WP'leri silinecek!"): return
        self._send({"type": "wp_clear"})
        self._set_phase(PHASE_IDLE, "WP SİLİNİYOR")

    def cmd_task1(self):
        if not messagebox.askyesno("Görev 1", "Figure-8 ×2 görevini başlat?\n\nWP sırası:\n  WP1=Direk1  WP2=Direk2  WP3=Pist"): return
        wps = self._map.waypoints
        if len(wps) != 3:
            messagebox.showwarning("Task1", "Tam 3 WP gerekli:\n  WP1=Direk1  WP2=Direk2  WP3=Pist"); return
        if self._awaiting_wp_ok:
            self._set_phase(PHASE_UPLOADING, "DEVAM EDİYOR"); return
        self._on_upload(wps, "TASK1", 3.0,
                        float(self._map.get_alt() or 10.0))

    def cmd_task2(self):
        if not messagebox.askyesno(
            "Görev 2",
            "Tarama + drop görevini başlat?\n\n"
            "WP sırası:\n  WP1-WP2 = Alana gidiş\n  WP3-WP4 = Tarama alanı köşeleri"
        ): return
        if self._map.get_mode() != "TASK2":
            messagebox.showwarning("Task2", "Harita modunu TASK2 yap."); return
        wps = self._map.waypoints
        if len(wps) != 4:
            messagebox.showwarning("Task2", "Tam 4 WP gerekli:\n  WP1-WP2 = Alana gidiş\n  WP3-WP4 = Tarama alanı köşeleri"); return
        if self._awaiting_wp_ok:
            self._set_phase(PHASE_UPLOADING, "DEVAM EDİYOR"); return
        self._on_upload(wps, "TASK2", self._map.get_spacing(), float(self._map.get_alt() or 10.0))

        def _vision():
            try:
                req = urllib.request.Request(
                    f"{RPI_STREAM_URL}/start", data=b"", method="POST")
                with urllib.request.urlopen(req, timeout=3) as r:
                    print(f"[Vision] {json.loads(r.read().decode())}")
            except Exception as e:
                print(f"[Vision] {e}")
        threading.Thread(target=_vision, daemon=True).start()





if __name__ == "__main__":
    app = DroneApp()
    app.mainloop()
