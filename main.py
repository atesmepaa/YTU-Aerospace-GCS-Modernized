"""
main.py — YTÜ Maçka GCS  (v2 — bölünmüş mimari)
Tüm bileşenleri bir araya getirir, mesaj yönlendirmesi ve görev kararları burada.
"""

import json
import threading
import urllib.request
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image

from config import (
    ARDU_MODE_DISPLAY,
    MOUSE_IMU_ENABLED,
    RPI_STREAM_URL,
)
from communication import SiKLink
from mission_logic import (
    generate_task1_figure8_waypoints,
    generate_task2_scan_waypoints,
    waypoints_to_payload,
    pts_to_payload,
)
from ui_components import CameraWidget, IMUWidget, MapWidget, PayloadWidget


ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class DroneApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("YTÜ Maçka GCS")
        self.attributes("-fullscreen", False)
        self.configure(fg_color="#FF9347")
        self.iconbitmap("logo.ico")

        # Görev durumu
        self._awaiting_wp_ok              = False
        self._pending_mission_after_upload: str | None = None

        # Jitter önleme
        self._last_shown_alt = None
        self._last_shown_spd = None
        self._ALT_THRESHOLD  = 0.3
        self._SPD_THRESHOLD  = 0.2

        # Mouse IMU state
        self._mouse_pitch  = 0.0
        self._mouse_roll   = 0.0
        self._mouse_drag   = False
        self._mouse_last_x = 0
        self._mouse_last_y = 0

        self._setup_ui()

        # SiK — callback'ler after() ile UI thread'e yönlendirilir
        self._sik = SiKLink(
            on_message    = lambda m: self.after(0, lambda msg=m: self._handle_msg(msg)),
            on_link_status= lambda s: self.after(0, lambda st=s: self.lbl_comm.configure(
                text=f"BAĞLANTI: {st}")),
        )
        self._sik.start()

        if MOUSE_IMU_ENABLED:
            self._start_mouse_imu()

    # ------------------------------------------------------------------
    # SiK gönderim kısayolu
    # ------------------------------------------------------------------
    def _send(self, obj: dict):
        self._sik.send(obj)

    # ------------------------------------------------------------------
    # Gelen mesajları yönlendir
    # ------------------------------------------------------------------
    def _handle_msg(self, msg: dict):
        t = msg.get("type")

        if t == "battery":
            rem       = msg.get("rem", 0)
            current_a = msg.get("current_a", 0)
            voltage_v = msg.get("voltage_v", 0)
            self.lbl_bat.configure(text=f"BATARYA: %{rem}  ({voltage_v} V)")
            self.lbl_current.configure(text=f"AKIM: {current_a} A")

        elif t == "mode":
            raw_mode = msg.get("mode", "")
            armed    = msg.get("armed", False)
            display  = ARDU_MODE_DISPLAY.get(raw_mode, raw_mode)
            arm_str  = "🔴 ARM" if armed else "⚪ DISARM"
            self.lbl_mode.configure(text=f"MOD: {display}  {arm_str}")

        elif t == "alt":
            new_alt = abs(float(msg.get("rel_m", 0)))
            if self._last_shown_alt is None or abs(new_alt - self._last_shown_alt) >= self._ALT_THRESHOLD:
                self._last_shown_alt = new_alt
                self.lbl_alt.configure(text=f"İRTİFA: {new_alt:.1f} m")

        elif t == "speed":
            new_spd = float(msg.get("mps", 0))
            if self._last_shown_spd is None or abs(new_spd - self._last_shown_spd) >= self._SPD_THRESHOLD:
                self._last_shown_spd = new_spd
                self.lbl_speed.configure(text=f"HIZ: {new_spd:.1f} m/s")

        elif t == "gps":
            fix      = msg.get("fix", False)
            sats     = msg.get("sats", 0)
            fix_type = msg.get("fix_type", 0)
            fix_str  = f"FIX ({fix_type}D)" if fix else "FIX YOK"
            self.lbl_gps.configure(text=f"GPS: {fix_str}  {sats} uydu")

        elif t == "att":
            yaw = float(msg.get("yaw", 0.0))
            self.lbl_yaw.configure(text=f"YAW: {yaw:.0f}°")
            if not MOUSE_IMU_ENABLED:
                self._imu.update(float(msg.get("pitch", 0.0)), float(msg.get("roll", 0.0)))

        elif t == "pos":
            lat = msg.get("lat")
            lon = msg.get("lon")
            if lat is not None and lon is not None:
                self._map.update_drone_pos(float(lat), float(lon))

        elif t == "payload":
            THRESHOLD = 1500
            self._payload.set_payload1(msg.get("p1_raw", 0) > THRESHOLD)
            self._payload.set_payload2(msg.get("p2_raw", 0) > THRESHOLD)

        elif t == "timer":
            sec = int(msg.get("sec", 0))
            m, s = divmod(sec, 60)
            self.lbl_timer.configure(text=f"GÖREV SÜRESİ: {m:02d}:{s:02d}")

        elif t == "pc_link":
            self.lbl_comm.configure(text=f"BAĞLANTI: {msg.get('status', 'KOPUK')}")

        elif t == "status":
            status_msg = msg.get("msg", "")
            self.lbl_status.configure(text=f"DURUM: {status_msg}")

            if status_msg == "wp_upload_ok":
                self._awaiting_wp_ok = False
                pending = self._pending_mission_after_upload
                self._pending_mission_after_upload = None
                if pending in ("task1", "task2"):
                    self._send({"type": "mission", "name": pending})
                    self.lbl_status.configure(text=f"DURUM: {pending.upper()} başlatıldı (AUTO)")

            elif status_msg == "wp_clear_ok":
                self.lbl_status.configure(text="DURUM: Pixhawk WP temizlendi ✓")

    # ------------------------------------------------------------------
    # UI kurulum
    # ------------------------------------------------------------------
    def _setup_ui(self):
        self.grid_columnconfigure(0, weight=7)
        self.grid_columnconfigure(1, weight=3)
        self.grid_rowconfigure(0, weight=1)

        # Sol panel
        self.left_panel = ctk.CTkFrame(self, corner_radius=25, fg_color="#e4e4e4")
        self.left_panel.grid(row=0, column=0, sticky="nsew", padx=10, pady=20)
        self.left_panel.grid_rowconfigure(0, weight=0)
        self.left_panel.grid_rowconfigure(1, weight=1)
        self.left_panel.grid_rowconfigure(2, weight=0)
        self.left_panel.grid_columnconfigure(0, weight=1)

        # Telemetri etiketleri
        self.data_frame = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        self.data_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        self.data_frame.grid_columnconfigure((0, 1), weight=1)
        self._row_idx = 0
        self._col_idx = 0

        self.lbl_mode    = self._lbl("MOD: BEKLENİYOR")
        self.lbl_alt     = self._lbl("İRTİFA: 0.0 m")
        self.lbl_bat     = self._lbl("BATARYA: %0  (0.0 V)")
        self.lbl_gps     = self._lbl("GPS: FIX BEKLENİYOR")
        self.lbl_speed   = self._lbl("HIZ: 0.0 m/s")
        self.lbl_yaw     = self._lbl("YAW: 0°")
        self.lbl_current = self._lbl("AKIM: 0.0 A")
        self.lbl_timer   = self._lbl("GÖREV SÜRESİ: 00:00")
        self.lbl_comm    = self._lbl("BAĞLANTI: BEKLENİYOR")
        self.lbl_status  = self._lbl("DURUM: HAZIR")

        # IMU (üst sağ köşe, absolute)
        self._imu = IMUWidget(self.left_panel)
        self._imu.place(relx=0.98, rely=0.02, anchor="ne")

        # Kamera + Harita
        bottom = ctk.CTkFrame(self.left_panel, fg_color="transparent")
        bottom.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        bottom.grid_columnconfigure(0, weight=1)
        bottom.grid_columnconfigure(1, weight=1)
        bottom.grid_rowconfigure(0, weight=1)

        self._cam = CameraWidget(bottom)
        self._cam.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        self._map = MapWidget(bottom, on_upload_request=self._on_map_upload_request)
        self._map.grid(row=0, column=1, sticky="nsew", padx=(5, 0))

        # Sağ panel
        self.right_panel = ctk.CTkFrame(self, fg_color="#FF9347")
        self.right_panel.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)

        # Logo
        logo_container = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        logo_container.pack(side="top", pady=10)
        try:
            img = Image.open("logo.png")
        except FileNotFoundError:
            img = Image.new("RGBA", (250, 220), (255, 147, 71, 255))
        self._logo_img = ctk.CTkImage(light_image=img, dark_image=img, size=(250, 220))
        ctk.CTkLabel(logo_container, image=self._logo_img, text="").pack()

        # Payload
        bg = self.right_panel.cget("fg_color")
        bg = bg[0] if isinstance(bg, (list, tuple)) else bg
        self._payload = PayloadWidget(self.right_panel, bg_color=bg)
        self._payload.pack(pady=(10, 20))

        # Komut butonları
        self._btn("MANUEL UÇUŞ (HOLD)",   "#e4e4e4", "#2C3E50", self.cmd_hold)
        self._btn("RTL (BAŞA DÖN)",        "#e4e4e4", "#2C3E50", self.cmd_rtl)
        self._btn("LAND (İNİŞ)",           "#e4e4e4", "#2C3E50", self.cmd_land)
        self._btn("WP RESET",              "#6b1a1a", "#e4e4e4", self.cmd_wp_clear)
        self._btn("GÖREV 1 (MANEVRA)",     "#e4e4e4", "#2C3E50", self.cmd_task1)
        self._btn("GÖREV 2 (YÜK TAŞIMA)", "#e4e4e4", "#2C3E50", self.cmd_task2)
        self._btn("ACİL DURUM",            "red",     "darkred", self.cmd_kill)

        ctk.CTkFrame(self.right_panel, fg_color="transparent").pack(expand=2)

    def _lbl(self, text: str) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(
            self.data_frame, text=text,
            text_color="#cf6d24", font=("Roboto", 20, "bold"), anchor="w",
        )
        lbl.grid(row=self._row_idx, column=self._col_idx, sticky="w", padx=20, pady=4)
        self._col_idx += 1
        if self._col_idx > 1:
            self._col_idx = 0
            self._row_idx += 1
        return lbl

    def _btn(self, text, color, hover_color, command):
        ctk.CTkButton(
            self.right_panel, text=text, text_color="black",
            fg_color=color, hover_color=hover_color,
            font=("Roboto", 18, "bold"), command=command,
            width=260, height=45,
        ).pack(pady=8)

    # ------------------------------------------------------------------
    # Mouse IMU simülatörü
    # ------------------------------------------------------------------
    def _start_mouse_imu(self):
        self.bind("<ButtonPress-1>",   self._mp)
        self.bind("<B1-Motion>",       self._mm)
        self.bind("<ButtonRelease-1>", self._mr)
        self.bind("<ButtonPress-3>",   self._mreset)
        ctk.CTkLabel(self.left_panel, text="© YTÜ Maçka Aerospace",
                     text_color="#aaa", font=("Roboto", 10),
                     fg_color="transparent").grid(row=2, column=0, pady=(0, 4))

    def _mp(self, e):
        self._mouse_drag = True
        self._mouse_last_x, self._mouse_last_y = e.x_root, e.y_root

    def _mm(self, e):
        if not self._mouse_drag:
            return
        dx = e.x_root - self._mouse_last_x
        dy = e.y_root - self._mouse_last_y
        self._mouse_last_x, self._mouse_last_y = e.x_root, e.y_root
        self._mouse_roll  = max(-90.0, min(90.0, self._mouse_roll  + dx * 0.225))
        self._mouse_pitch = max(-90.0, min(90.0, self._mouse_pitch - dy * 0.225))
        self._imu.update(self._mouse_pitch, self._mouse_roll)

    def _mr(self, e):
        self._mouse_drag = False

    def _mreset(self, e):
        self._mouse_pitch = self._mouse_roll = 0.0
        self._imu.update(0.0, 0.0)

    # ------------------------------------------------------------------
    # Harita → upload callback
    # ------------------------------------------------------------------
    def _on_map_upload_request(self, waypoints: list, mode: str, spacing_m: float, alt: float):
        if not waypoints:
            messagebox.showwarning("Uyarı", "Gönderilecek waypoint yok!")
            return
        if len(waypoints) == 1:
            messagebox.showwarning("Uyarı", "En az 2 waypoint gerekli!")
            return
        if self._awaiting_wp_ok:
            self.lbl_status.configure(text="DURUM: Zaten yükleme devam ediyor...")
            return

        if len(waypoints) == 2 and mode == "TASK1":
            pts = generate_task1_figure8_waypoints(waypoints, n_per_circle=8)
            if len(pts) < 3:
                messagebox.showwarning("Task1", "Figure-8 üretilemedi. WP'ler yeterince uzak mı?")
                return
            wp_list = pts_to_payload(pts, alt)
            self._awaiting_wp_ok = True
            self._pending_mission_after_upload = "task1"
            self._send({"type": "wp_upload", "waypoints": wp_list, "mission": "task1"})
            self.lbl_status.configure(text=f"DURUM: TASK1 figure-8 yükleniyor ({len(wp_list)} WP).")

        elif len(waypoints) == 2 and mode == "TASK2":
            pts = generate_task2_scan_waypoints(waypoints, spacing_m=spacing_m)
            if len(pts) < 2:
                messagebox.showwarning("Task2", "Tarama waypoint üretilemedi.")
                return
            wp_list = pts_to_payload(pts, alt)
            self._awaiting_wp_ok = True
            self._pending_mission_after_upload = "task2"
            self._send({"type": "wp_upload", "waypoints": wp_list, "mission": "task2"})
            self.lbl_status.configure(text=f"DURUM: TASK2 tarama yükleniyor ({len(wp_list)} WP).")

        else:
            # 3+ WP — normal TASK1 upload
            wp_list = waypoints_to_payload(waypoints, alt)
            self._awaiting_wp_ok = True
            self._pending_mission_after_upload = "task1"
            self._send({"type": "wp_upload", "waypoints": wp_list, "mission": "task1"})
            self.lbl_status.configure(text=f"DURUM: TASK1 WP yükleniyor ({len(wp_list)} WP).")

    # ------------------------------------------------------------------
    # Komutlar
    # ------------------------------------------------------------------
    def cmd_hold(self):
        self._send({"type": "cmd", "name": "hold"})

    def cmd_rtl(self):
        self._send({"type": "cmd", "name": "rtl"})

    def cmd_land(self):
        self._send({"type": "cmd", "name": "land"})

    def cmd_wp_clear(self):
        if not messagebox.askyesno("WP Sil", "Pixhawk'taki tüm waypointler silinecek! Emin misin?"):
            return
        self._send({"type": "wp_clear"})
        self.lbl_status.configure(text="DURUM: Pixhawk WP silme isteği gönderildi")

    def cmd_task1(self):
        if not messagebox.askyesno("Task1", "Birinci görevi başlatacağından emin misin?"):
            return
        wps = self._map.waypoints
        if len(wps) < 2:
            messagebox.showwarning("Task1", "Task1 için en az 2 waypoint seçmelisin.")
            return
        if self._awaiting_wp_ok:
            self.lbl_status.configure(text="DURUM: Zaten yükleme devam ediyor...")
            return
        self._on_map_upload_request(wps, self._map.get_mode(), spacing_m=6.0, alt=20.0)

    def cmd_task2(self):
        if not messagebox.askyesno("Task2", "İkinci görevi başlatacağından emin misin?"):
            return
        if self._map.get_mode() != "TASK2":
            messagebox.showwarning("Task2", "Map modu TASK2 değil. Sağ üstten TASK2 seç.")
            return
        wps = self._map.waypoints
        if len(wps) != 2:
            messagebox.showwarning("Task2", "Task2 için tam 2 waypoint seçmelisin.")
            return
        if self._awaiting_wp_ok:
            self.lbl_status.configure(text="DURUM: Zaten yükleme devam ediyor...")
            return

        self._on_map_upload_request(wps, "TASK2", spacing_m=6.0, alt=20.0)

        # Vision sistemi başlat (arka plan)
        def _start_vision():
            try:
                req = urllib.request.Request(
                    f"{RPI_STREAM_URL}/start", data=b"", method="POST")
                with urllib.request.urlopen(req, timeout=3) as resp:
                    print(f"[UI] Vision start: {json.loads(resp.read().decode())}")
            except Exception as e:
                print(f"[UI] Vision start hatası: {e}")
        threading.Thread(target=_start_vision, daemon=True).start()

    def cmd_kill(self):
        if messagebox.askyesno("TEHLİKE", "Motorlar kapatılacak! Emin misin?"):
            self._send({"type": "cmd", "name": "kill"})


if __name__ == "__main__":
    app = DroneApp()
    app.mainloop()