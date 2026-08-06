"""
ui_components.py
~~~~~~~~~~~~~~~~
Uygulamanın özel widget'larını içerir:
  - IMUWidget       : Pitch/Roll görsel göstergesi
  - PayloadWidget   : Yük durumu (yeşil/kırmızı)
  - CameraWidget    : MJPEG canlı akışı
  - MapWidget       : GPS haritası, WP yönetimi, TASK1/TASK2 önizleme
"""

import io
import math
import threading
import time
import urllib.request

import customtkinter as ctk
from PIL import Image

from config import RPI_STREAM_URL, CAM_DISPLAY_SIZE
from mission_logic import (
    generate_task1_figure8_waypoints,
    generate_task2_scan_waypoints,
)


# ======================================================================
# IMU Widget
# ======================================================================

class IMUWidget(ctk.CTkFrame):
    _SIZE      = (120, 120)
    _FPS_LIMIT = 0.04  # ~25 FPS

    def __init__(self, parent, **kwargs):
        super().__init__(parent, corner_radius=18, fg_color="transparent", **kwargs)
        try:
            base = Image.open("imu.png").convert("RGBA").resize(self._SIZE, Image.LANCZOS)
        except FileNotFoundError:
            base = Image.new("RGBA", self._SIZE, (80, 80, 80, 255))
        self._base_img    = base
        self._last_update = 0.0

        self._ctk_img = ctk.CTkImage(
            light_image=self._base_img, dark_image=self._base_img, size=self._SIZE
        )
        self._img_label = ctk.CTkLabel(self, image=self._ctk_img, text="")
        self._img_label.pack()

        self._text_label = ctk.CTkLabel(
            self,
            text="Pitch: 0.0°   Roll: 0.0°",
            text_color="#cf6d24",
            font=("Roboto", 14, "bold"),
        )
        self._text_label.pack(pady=(2, 0))

    def update(self, pitch_deg: float, roll_deg: float):
        now = time.time()
        if now - self._last_update < self._FPS_LIMIT:
            return
        self._last_update = now
        rotated = self._base_img.rotate(-roll_deg, resample=Image.BICUBIC, expand=False)
        self._ctk_img = ctk.CTkImage(light_image=rotated, dark_image=rotated, size=self._SIZE)
        self._img_label.configure(image=self._ctk_img)
        self._text_label.configure(text=f"Pitch: {pitch_deg:.1f}°   Roll: {roll_deg:.1f}°")


# ======================================================================
# Payload Widget
# ======================================================================

class PayloadWidget(ctk.CTkFrame):
    def __init__(self, parent, bg_color: str, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._canvas = ctk.CTkCanvas(self, width=200, height=60,
                                     bg=bg_color, highlightthickness=0)
        self._canvas.pack()
        self._p1 = self._canvas.create_oval(20,  10, 60,  50, fill="red")
        self._p2 = self._canvas.create_oval(120, 10, 160, 50, fill="red")
        self._canvas.create_text(40,  55, text="YÜK 1", fill="white", font=("Roboto", 10, "bold"))
        self._canvas.create_text(140, 55, text="YÜK 2", fill="white", font=("Roboto", 10, "bold"))

    def set_payload1(self, has_load: bool):
        self._canvas.itemconfig(self._p1, fill="green" if has_load else "red")

    def set_payload2(self, has_load: bool):
        self._canvas.itemconfig(self._p2, fill="green" if has_load else "red")


# ======================================================================
# Camera Widget
# ======================================================================

class CameraWidget(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="#2b2b2b", corner_radius=12, **kwargs)
        self._status = ctk.CTkLabel(self, text="📷 Kamera bekleniyor...",
                                    text_color="#888", font=("Roboto", 12))
        self._status.pack(pady=(6, 2))
        self._img_lbl = ctk.CTkLabel(self, text="")
        self._img_lbl.pack(expand=True)
        self._start_stream()

    def _start_stream(self):
        def cam_loop():
            stream = None
            while True:
                try:
                    stream = urllib.request.urlopen(RPI_STREAM_URL, timeout=5)
                    buf = b""
                    while True:
                        buf += stream.read(4096)
                        s = buf.find(b"\xff\xd8")
                        e = buf.find(b"\xff\xd9")
                        if s != -1 and e != -1 and e > s:
                            jpg = buf[s:e + 2]
                            buf = buf[e + 2:]
                            img = Image.open(io.BytesIO(jpg)).resize(CAM_DISPLAY_SIZE, Image.LANCZOS)
                            ci  = ctk.CTkImage(light_image=img, dark_image=img, size=CAM_DISPLAY_SIZE)
                            self.after(0, lambda i=ci: self._show(i))
                except Exception:
                    self.after(0, lambda: self._status.configure(
                        text="📷 Kamera bağlanamadı, yeniden deneniyor..."))
                    if stream:
                        try: stream.close()
                        except Exception: pass
                    time.sleep(2.0)
        threading.Thread(target=cam_loop, daemon=True).start()

    def _show(self, img):
        self._img_lbl.configure(image=img, text="")
        self._status.configure(text="📷 Canlı Kamera")


# ======================================================================
# Map Widget
# ======================================================================

class MapWidget(ctk.CTkFrame):
    MAP_W  = 420
    MAP_H  = 380
    MARGIN = 30

    def __init__(self, parent, on_upload_request, **kwargs):
        """
        on_upload_request(waypoints, mode, spacing_m, alt) çağrılır;
        asıl gönderme mantığı main.py'de kalır.
        """
        super().__init__(parent, fg_color="#1a1a2e", corner_radius=12, **kwargs)
        self._on_upload_request = on_upload_request

        self.waypoints   = []
        self.drone_trail = []
        self.drone_lat   = None
        self.drone_lon   = None

        self._map_mode = ctk.StringVar(value="TASK2")

        self._build_ui()
        self._draw_map()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def update_drone_pos(self, lat: float, lon: float):
        self.drone_lat = lat
        self.drone_lon = lon
        if not self.drone_trail or (lat, lon) != self.drone_trail[-1]:
            self.drone_trail.append((lat, lon))
            if len(self.drone_trail) > 500:
                self.drone_trail.pop(0)
        self._draw_map()

    def get_mode(self) -> str:
        return self._map_mode.get()

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------
    def _build_ui(self):
        ctk.CTkLabel(self, text="🗺️ Konum Haritası",
                     text_color="#aaa", font=("Roboto", 12)).pack(pady=(6, 2))

        container = ctk.CTkFrame(self, fg_color="#1a1a2e", corner_radius=0)
        container.pack(expand=True, fill="both", padx=6, pady=6)

        self._canvas = ctk.CTkCanvas(container, bg="#1a1a2e", highlightthickness=0)
        self._canvas.pack(expand=True, fill="both")
        self._canvas.bind("<Configure>", lambda e: self._draw_map())

        # Mod toggle — canvas üstünde
        self._mode_toggle = ctk.CTkSegmentedButton(
            container,
            values=["TASK1", "TASK2"],
            variable=self._map_mode,
            command=self._on_mode_change,
            height=28,
            corner_radius=10,
        )
        self._mode_toggle.place(relx=0.98, rely=0.06, anchor="ne")

        self._wait_label = ctk.CTkLabel(
            container,
            text="GPS verisi bekleniyor...",
            text_color="#555",
            font=("Roboto", 13),
            fg_color="transparent",
        )
        self._wait_label.place(relx=0.5, rely=0.5, anchor="center")

        # Buton satırı 1
        row1 = ctk.CTkFrame(self, fg_color="transparent")
        row1.pack(pady=(0, 4))
        ctk.CTkButton(row1, text="+ WP Ekle",      font=("Roboto", 11), height=28, width=110,
                      command=self._add_wp).pack(side="left", padx=2)
        ctk.CTkButton(row1, text="İzi Temizle",    font=("Roboto", 11), height=28, width=100,
                      fg_color="#555", command=self._clear_trail).pack(side="left", padx=2)
        ctk.CTkButton(row1, text="🗑️ WP Temizle", font=("Roboto", 11), height=28, width=110,
                      fg_color="#6b1a1a", hover_color="#5c1414",
                      command=self._clear_wps).pack(side="left", padx=2)

        # Buton satırı 2
        row2 = ctk.CTkFrame(self, fg_color="transparent")
        row2.pack(pady=(0, 6))
        ctk.CTkLabel(row2, text="İrtifa (m):", font=("Roboto", 11), text_color="#aaa").pack(
            side="left", padx=(0, 4))
        self._alt_entry = ctk.CTkEntry(row2, width=30, height=26, placeholder_text="20")
        self._alt_entry.pack(side="left", padx=(0, 6))
        ctk.CTkButton(row2, text="📤 Pixhawk'a Gönder",
                      font=("Roboto", 11), height=28, width=150,
                      fg_color="#1a6b3c", hover_color="#145c30",
                      command=self._request_upload).pack(side="left")
        ctk.CTkLabel(row2, text="Şerit (m):", font=("Roboto", 11), text_color="#aaa").pack(
            side="left", padx=(8, 4))
        self._spacing_entry = ctk.CTkEntry(row2, width=30, height=26, placeholder_text="6")
        self._spacing_entry.pack(side="left", padx=(0, 6))

    # ------------------------------------------------------------------
    # GPS → piksel
    # ------------------------------------------------------------------
    def _gps_to_px(self, lat, lon):
        if not self.waypoints and self.drone_lat is None:
            return self.MAP_W // 2, self.MAP_H // 2

        cw = self._canvas.winfo_width()  or self.MAP_W
        ch = self._canvas.winfo_height() or self.MAP_H

        all_lats = [wp[0] for wp in self.waypoints]
        all_lons = [wp[1] for wp in self.waypoints]
        if self.drone_lat is not None:
            all_lats.append(self.drone_lat)
            all_lons.append(self.drone_lon)
        all_lats += [p[0] for p in self.drone_trail]
        all_lons += [p[1] for p in self.drone_trail]
        all_lats.append(lat)
        all_lons.append(lon)

        center_lat = (min(all_lats) + max(all_lats)) / 2.0
        center_lon = (min(all_lons) + max(all_lons)) / 2.0

        lat_per_m = 1.0 / 111320.0
        lon_per_m = 1.0 / (111320.0 * max(0.01, math.cos(math.radians(center_lat))))

        MIN_SPAN_M, MAX_SPAN_M = 300.0, 1000.0

        span_lat_m = max(MIN_SPAN_M, min(MAX_SPAN_M, (max(all_lats) - min(all_lats)) / lat_per_m))
        span_lon_m = max(MIN_SPAN_M, min(MAX_SPAN_M, (max(all_lons) - min(all_lons)) / lon_per_m))
        span_lat   = span_lat_m * lat_per_m
        span_lon   = span_lon_m * lon_per_m
        min_lat    = center_lat - span_lat / 2.0
        min_lon    = center_lon - span_lon / 2.0

        usable_w = cw - 2 * self.MARGIN
        usable_h = ch - 2 * self.MARGIN

        px = self.MARGIN + (lon - min_lon) / span_lon * usable_w
        py = self.MARGIN + (center_lat + span_lat / 2.0 - lat) / span_lat * usable_h
        return int(px), int(py)

    # ------------------------------------------------------------------
    # Çizim
    # ------------------------------------------------------------------
    def _draw_map(self):
        c = self._canvas
        c.delete("all")
        w = c.winfo_width()  or self.MAP_W
        h = c.winfo_height() or self.MAP_H

        # Izgara
        for i in range(0, w, 40):
            c.create_line(i, 0, i, h, fill="#2a2a4a", width=1)
        for i in range(0, h, 40):
            c.create_line(0, i, w, i, fill="#2a2a4a", width=1)

        # İz
        if len(self.drone_trail) >= 2:
            for i in range(1, len(self.drone_trail)):
                x1, y1 = self._gps_to_px(*self.drone_trail[i - 1])
                x2, y2 = self._gps_to_px(*self.drone_trail[i])
                c.create_line(x1, y1, x2, y2, fill="#00aaff", width=2)

        # WP'ler
        for idx, (wlat, wlon, label) in enumerate(self.waypoints):
            wx, wy = self._gps_to_px(wlat, wlon)
            c.create_oval(wx - 7, wy - 7, wx + 7, wy + 7,
                          fill="#f0a500", outline="#fff", width=2)
            c.create_text(wx, wy - 14, text=f"WP{idx+1} {label}",
                          fill="#f0a500", font=("Roboto", 9, "bold"))

        # Mod bazlı önizleme
        if self._map_mode.get() == "TASK2":
            self._draw_task2_rect()
        else:
            self._draw_task1_figure8()

        # Drone
        if self.drone_lat is not None:
            self._wait_label.place_forget()
            dx, dy = self._gps_to_px(self.drone_lat, self.drone_lon)
            c.create_polygon(dx, dy - 10, dx - 7, dy + 7, dx, dy + 3, dx + 7, dy + 7,
                             fill="#00ff88", outline="#fff", width=1)
            c.create_text(dx, dy + 18,
                          text=f"{self.drone_lat:.5f}, {self.drone_lon:.5f}",
                          fill="#00ff88", font=("Roboto", 8))
        else:
            self._wait_label.place(relx=0.5, rely=0.5, anchor="center")

    def _draw_task2_rect(self):
        if len(self.waypoints) != 2:
            return
        (lat1, lon1, _), (lat2, lon2, _) = self.waypoints
        lat_min, lat_max = sorted([lat1, lat2])
        lon_min, lon_max = sorted([lon1, lon2])
        x1, y1 = self._gps_to_px(lat_max, lon_min)
        x2, y2 = self._gps_to_px(lat_min, lon_max)
        self._canvas.create_rectangle(x1, y1, x2, y2, outline="#ff3b3b", width=3)
        self._canvas.create_text((x1 + x2) // 2, y1 - 10,
                                 text="TASK2 ALANI (2 WP)",
                                 fill="#ff3b3b", font=("Roboto", 10, "bold"))

    def _draw_task1_figure8(self):
        if len(self.waypoints) != 2:
            return
        pts = generate_task1_figure8_waypoints(self.waypoints, n_per_circle=8)
        if len(pts) < 3:
            return

        pixel_pts = [self._gps_to_px(lat, lon) for lat, lon in pts]
        for i in range(1, len(pixel_pts)):
            x1, y1 = pixel_pts[i - 1]
            x2, y2 = pixel_pts[i]
            self._canvas.create_line(x1, y1, x2, y2, fill="#ff9f00", width=2, dash=(4, 3))

        # Merkez noktaları
        for (clat, clon, _), lbl in zip(self.waypoints[:2], ["M1", "M2"]):
            cx, cy = self._gps_to_px(clat, clon)
            self._canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5,
                                     outline="#ff9f00", fill="#1a1a2e", width=2)
            self._canvas.create_text(cx, cy - 14, text=lbl,
                                     fill="#ff9f00", font=("Roboto", 9, "bold"))

        # Start/End
        sx, sy = pixel_pts[0]
        self._canvas.create_oval(sx - 5, sy - 5, sx + 5, sy + 5,
                                 fill="#00ff88", outline="#fff", width=2)
        self._canvas.create_text(sx, sy - 14, text="START/END",
                                 fill="#00ff88", font=("Roboto", 8, "bold"))

        # Yarıçap bilgisi
        (lat1, lon1, _), (lat2, lon2, _) = self.waypoints[0], self.waypoints[1]
        ref_lat = (lat1 + lat2) / 2.0
        dlat_m  = (lat2 - lat1) * 111320.0
        dlon_m  = (lon2 - lon1) * 111320.0 * math.cos(math.radians(ref_lat))
        r_m     = math.sqrt(dlat_m ** 2 + dlon_m ** 2) / 2.0
        px1 = self._gps_to_px(lat1, lon1)
        px2 = self._gps_to_px(lat2, lon2)
        mid_x = (px1[0] + px2[0]) // 2
        mid_y = (px1[1] + px2[1]) // 2
        self._canvas.create_text(mid_x, mid_y + 20,
                                 text=f"r≈{r_m:.0f}m  |  18 WP",
                                 fill="#ff9f00", font=("Roboto", 9))

    # ------------------------------------------------------------------
    # WP aksiyonları
    # ------------------------------------------------------------------
    def _add_wp(self):
        if self.drone_lat is None:
            return
        self.waypoints.append((self.drone_lat, self.drone_lon, str(len(self.waypoints) + 1)))
        self._draw_map()

    def _clear_trail(self):
        self.drone_trail.clear()
        self._draw_map()

    def _clear_wps(self):
        self.waypoints.clear()
        self._draw_map()

    def _on_mode_change(self, v: str):
        self._draw_map()

    def _request_upload(self):
        try:
            alt = float(self._alt_entry.get() or "20")
        except ValueError:
            alt = 20.0
        try:
            spacing = float(self._spacing_entry.get() or "6")
            spacing = max(2.0, spacing)
        except Exception:
            spacing = 6.0

        self._on_upload_request(
            waypoints=self.waypoints,
            mode=self._map_mode.get(),
            spacing_m=spacing,
            alt=alt,
        )