"""
ui_components.py — YTÜ Maçka GCS  (v3 — yeniden tasarım)
Dark tactical aesthetic: siyah paneller, turuncu vurgular, Consolas font.
"""

import io
import math
import threading
import time
import urllib.request

import customtkinter as ctk
from PIL import Image

from config import RPI_STREAM_URL, CAM_DISPLAY_SIZE, THEME
from mission_logic import generate_task1_figure8_waypoints, generate_task2_scan_waypoints

T = THEME  # kısa alias


# ─────────────────────────────────────────────
# Yardımcı: köşeli "kart" frame
# ─────────────────────────────────────────────
def _card(parent, **kwargs):
    defaults = dict(corner_radius=6, fg_color=T["bg_card"], border_width=1,
                    border_color=T["border"])
    defaults.update(kwargs)
    return ctk.CTkFrame(parent, **defaults)


# ─────────────────────────────────────────────
# IMU Widget
# ─────────────────────────────────────────────
class IMUWidget(ctk.CTkFrame):
    _SIZE      = (90, 90)
    _FPS_LIMIT = 0.04

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        try:
            base = Image.open("imu.png").convert("RGBA").resize(self._SIZE, Image.LANCZOS)
        except FileNotFoundError:
            base = Image.new("RGBA", self._SIZE, (30, 30, 30, 255))
        self._base        = base
        self._last_update = 0.0

        self._ctk_img = ctk.CTkImage(light_image=base, dark_image=base, size=self._SIZE)
        self._img_lbl = ctk.CTkLabel(self, image=self._ctk_img, text="")
        self._img_lbl.pack()

        self._txt_lbl = ctk.CTkLabel(
            self,
            text="P: 0.0°  R: 0.0°",
            text_color=T["accent"],
            font=(T["font_family"], 11, "bold"),
        )
        self._txt_lbl.pack(pady=(2, 0))

    def update(self, pitch: float, roll: float):
        now = time.time()
        if now - self._last_update < self._FPS_LIMIT:
            return
        self._last_update = now
        rotated = self._base.rotate(-roll, resample=Image.BICUBIC, expand=False)
        self._ctk_img = ctk.CTkImage(light_image=rotated, dark_image=rotated, size=self._SIZE)
        self._img_lbl.configure(image=self._ctk_img)
        self._txt_lbl.configure(text=f"P: {pitch:.1f}°  R: {roll:.1f}°")


# ─────────────────────────────────────────────
# Payload Widget
# ─────────────────────────────────────────────
class PayloadWidget(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        inner = _card(self)
        inner.pack(fill="x", padx=4, pady=2)

        # Başlık
        ctk.CTkLabel(inner, text="PAYLOAD", font=(T["font_family"], 9, "bold"),
                     text_color=T["text_secondary"]).pack(pady=(6, 2))

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(pady=(0, 8))

        self._c = ctk.CTkCanvas(row, width=140, height=44,
                                bg=T["bg_card"], highlightthickness=0)
        self._c.pack()

        # İki gösterge kutusu
        self._p1 = self._c.create_rectangle(6,  6, 62, 38, fill=T["danger"], outline=T["border"], width=1)
        self._p2 = self._c.create_rectangle(78, 6, 134, 38, fill=T["danger"], outline=T["border"], width=1)
        self._c.create_text(34, 22, text="YÜK 1", fill="white", font=(T["font_family"], 9, "bold"))
        self._c.create_text(106, 22, text="YÜK 2", fill="white", font=(T["font_family"], 9, "bold"))

    def set_payload1(self, ok: bool):
        self._c.itemconfig(self._p1, fill=T["ok"] if ok else T["danger"])

    def set_payload2(self, ok: bool):
        self._c.itemconfig(self._p2, fill=T["ok"] if ok else T["danger"])


# ─────────────────────────────────────────────
# Telemetri satırı yardımcısı
# ─────────────────────────────────────────────
class TelemetryGrid(ctk.CTkFrame):
    """2 sütunlu kompakt telemetri etiketi ızgarası."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._row = 0
        self._col = 0
        self._max_col = 4   # 0..4 => 5 sütun

    def add(self, key: str, default: str):
        cell = _card(self, fg_color=T["bg_card_dark"])
        cell.grid(row=self._row, column=self._col, sticky="ew", padx=2, pady=2)
        self.grid_columnconfigure(self._col, weight=1)

        ctk.CTkLabel(cell, text=key, font=(T["font_family"], 7),
                     text_color=T["text_secondary"], anchor="w").pack(
            fill="x", padx=6, pady=(2, 0))

        lbl = ctk.CTkLabel(cell, text=default,
                           font=(T["font_family"], 11, "bold"),
                           text_color=T["text_accent"], anchor="w")
        lbl.pack(fill="x", padx=6, pady=(0, 3))

        self._col += 1
        if self._col > self._max_col:
            self._col = 0
            self._row += 1
        return lbl


# ─────────────────────────────────────────────
# Camera Widget
# ─────────────────────────────────────────────
class CameraWidget(ctk.CTkFrame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color=T["bg_card_dark"],
                         corner_radius=6, border_width=1,
                         border_color=T["border"], **kwargs)

        # Başlık bar
        hdr = ctk.CTkFrame(self, fg_color=T["bg_card"], corner_radius=0, height=26)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        ctk.CTkLabel(hdr, text="● ", text_color=T["accent"],
                     font=(T["font_family"], 10)).pack(side="left", padx=(8, 0))
        self._title = ctk.CTkLabel(hdr, text="CAM — BEKLENIYOR",
                                   font=(T["font_family"], 10, "bold"),
                                   text_color=T["text_secondary"])
        self._title.pack(side="left")

        self._img_lbl = ctk.CTkLabel(self, text="")
        self._img_lbl.pack(expand=True)

        self._start()

    def _start(self):
        def loop():
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
                            jpg = buf[s:e+2]; buf = buf[e+2:]
                            img = Image.open(io.BytesIO(jpg)).resize(CAM_DISPLAY_SIZE, Image.LANCZOS)
                            ci  = ctk.CTkImage(light_image=img, dark_image=img, size=CAM_DISPLAY_SIZE)
                            self.after(0, lambda i=ci: self._show(i))
                except Exception:
                    self.after(0, lambda: self._title.configure(
                        text="CAM — BAĞLANTI YOK", text_color=T["danger"]))
                    if stream:
                        try: stream.close()
                        except: pass
                    time.sleep(2.0)
        threading.Thread(target=loop, daemon=True).start()

    def _show(self, img):
        self._img_lbl.configure(image=img, text="")
        self._title.configure(text="CAM — CANLI", text_color=T["ok"])


# ─────────────────────────────────────────────
# Map Widget
# ─────────────────────────────────────────────
class MapWidget(ctk.CTkFrame):
    MAP_W  = 420
    MAP_H  = 360
    MARGIN = 28

    def __init__(self, parent, on_upload_request, **kwargs):
        super().__init__(parent, fg_color=T["bg_card_dark"],
                         corner_radius=6, border_width=1,
                         border_color=T["border"], **kwargs)
        self._on_upload = on_upload_request
        self.waypoints   = []
        self.drone_trail = []
        self.drone_lat   = None
        self.drone_lon   = None
        self._map_mode   = ctk.StringVar(value="TASK2")
        self._build()
        self._draww()

    # ---------- public ----------
    def update_drone_pos(self, lat: float, lon: float):
        self.drone_lat, self.drone_lon = lat, lon
        if not self.drone_trail or (lat, lon) != self.drone_trail[-1]:
            self.drone_trail.append((lat, lon))
            if len(self.drone_trail) > 500:
                self.drone_trail.pop(0)
        self._draww()

    def get_mode(self) -> str:
        return self._map_mode.get()

    # ---------- build ----------
    def _build(self):
        # Başlık bar
        hdr = ctk.CTkFrame(self, fg_color=T["bg_card"], corner_radius=0, height=26)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        ctk.CTkLabel(hdr, text="● ", text_color=T["accent"],
                     font=(T["font_family"], 10)).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(hdr, text="MAP", font=(T["font_family"], 10, "bold"),
                     text_color=T["text_secondary"]).pack(side="left")

        # Mod toggle — başlık barı içinde sağa yaslanmış
        ctk.CTkSegmentedButton(
            hdr, values=["TASK1", "TASK2"],
            variable=self._map_mode,
            command=self._on_mode_change,
            height=20, corner_radius=4,
            fg_color=T["bg_card_dark"],
            selected_color=T["accent"],
            selected_hover_color=T["accent_dim"],
            unselected_color=T["bg_card_dark"],
            unselected_hover_color=T["border"],
            text_color=T["text_primary"],
            font=(T["font_family"], 9, "bold"),
        ).pack(side="right", padx=6, pady=3)

        # Canvas
        self._canvas = ctk.CTkCanvas(self, bg=T["map_bg"], highlightthickness=0)
        self._canvas.pack(expand=True, fill="both", padx=0, pady=0)
        self._canvas.bind("<Configure>", lambda e: self._draww())

        self._wait_lbl = ctk.CTkLabel(
            self._canvas, text="GPS VERİSİ BEKLENİYOR",
            text_color=T["text_secondary"],
            font=(T["font_family"], 11),
            fg_color="transparent",
        )
        self._wait_lbl.place(relx=0.5, rely=0.5, anchor="center")

        # Kontrol çubuğu
        ctrl = ctk.CTkFrame(self, fg_color=T["bg_card"], corner_radius=0, height=34)
        ctrl.pack(fill="x")
        ctrl.pack_propagate(False)

        def _cbtn(text, cmd, color=T["bg_card_dark"], w=80):
            return ctk.CTkButton(ctrl, text=text, command=cmd,
                                 fg_color=color, hover_color=T["accent_dim"],
                                 text_color=T["text_primary"],
                                 font=(T["font_family"], 9, "bold"),
                                 height=22, width=w, corner_radius=4)

        _cbtn("+ WP",     self._add_wp,      w=60).pack(side="left", padx=(6,2), pady=6)
        _cbtn("İZ SİL",   self._clear_trail, w=65, color="#1a2a1a").pack(side="left", padx=2, pady=6)
        _cbtn("WP SİL",   self._clear_wps,   w=65, color="#2a1a1a").pack(side="left", padx=2, pady=6)

        # İrtifa
        ctk.CTkLabel(ctrl, text="ALT:", font=(T["font_family"], 9),
                     text_color=T["text_secondary"]).pack(side="left", padx=(8,2))
        self._alt_e = ctk.CTkEntry(ctrl, width=36, height=22,
                                   placeholder_text="10",
                                   font=(T["font_family"], 9),
                                   fg_color=T["bg_card_dark"],
                                   border_color=T["border"],
                                   corner_radius=4)
        self._alt_e.pack(side="left", padx=(0,6))

        # Şerit — sadece TASK2'de görünür; her zaman pack'te kalır,
        # TASK1'de içerik gizlenir böylece sıra bozulmaz.
        self._spc_frame = ctk.CTkFrame(ctrl, fg_color="transparent")
        self._spc_frame.pack(side="left", padx=(0,0))

        self._spc_lbl = ctk.CTkLabel(self._spc_frame, text="SPACING:", font=(T["font_family"], 9),
                                     text_color=T["text_secondary"])
        self._spc_lbl.pack(side="left", padx=(0,2))
        self._spc_e = ctk.CTkEntry(self._spc_frame, width=32, height=22,
                                   placeholder_text="3",
                                   font=(T["font_family"], 9),
                                   fg_color=T["bg_card_dark"],
                                   border_color=T["border"],
                                   corner_radius=4)
        self._spc_e.pack(side="left", padx=(0,6))

        ctk.CTkButton(ctrl, text="📤 GÖNDER",
                      command=self._request_upload,
                      fg_color=T["accent"], hover_color=T["accent_dim"],
                      text_color="#000000",
                      font=(T["font_family"], 9, "bold"),
                      height=22, width=90, corner_radius=4).pack(side="left", padx=2)

        # Başlangıç moduna göre şerit frame görünürlüğünü ayarla
        self._update_spacing_visibility()

    # ---------- mod değişimi ----------
    def _on_mode_change(self, value: str):
        self._update_spacing_visibility()
        self._draww()

    def _update_spacing_visibility(self):
        if self._map_mode.get() == "TASK2":
            self._spc_lbl.pack(side="left", padx=(0,2))
            self._spc_e.pack(side="left", padx=(0,6))
        else:
            self._spc_lbl.pack_forget()
            self._spc_e.pack_forget()

    # ---------- GPS → piksel ----------
    def _gps_to_px(self, lat, lon):
        if not self.waypoints and self.drone_lat is None:
            return self.MAP_W // 2, self.MAP_H // 2

        cw = self._canvas.winfo_width()  or self.MAP_W
        ch = self._canvas.winfo_height() or self.MAP_H

        all_lats = [wp[0] for wp in self.waypoints] + [p[0] for p in self.drone_trail]
        all_lons = [wp[1] for wp in self.waypoints] + [p[1] for p in self.drone_trail]
        if self.drone_lat is not None:
            all_lats.append(self.drone_lat); all_lons.append(self.drone_lon)
        all_lats.append(lat); all_lons.append(lon)

        clat = (min(all_lats)+max(all_lats))/2
        clon = (min(all_lons)+max(all_lons))/2
        lpm  = 1/111320
        lopm = 1/(111320*max(0.01, math.cos(math.radians(clat))))
        MIN, MAX = 300, 1000
        slm = max(MIN, min(MAX, (max(all_lats)-min(all_lats))/lpm))
        snm = max(MIN, min(MAX, (max(all_lons)-min(all_lons))/lopm))
        sl  = slm*lpm; sn = snm*lopm
        uw  = cw - 2*self.MARGIN; uh = ch - 2*self.MARGIN
        px  = self.MARGIN + (lon-(clon-sn/2))/sn*uw
        py  = self.MARGIN + (clat+sl/2-lat)/sl*uh
        return int(px), int(py)

    # ---------- çizim ----------
    def _draww(self):
        c = self._canvas
        c.delete("all")
        w = c.winfo_width()  or self.MAP_W
        h = c.winfo_height() or self.MAP_H

        # Izgara — ince noktalı
        for i in range(0, w, 40):
            c.create_line(i, 0, i, h, fill=T["map_grid"], width=1)
        for i in range(0, h, 40):
            c.create_line(0, i, w, i, fill=T["map_grid"], width=1)

        # Drone izi
        if len(self.drone_trail) >= 2:
            for i in range(1, len(self.drone_trail)):
                x1,y1 = self._gps_to_px(*self.drone_trail[i-1])
                x2,y2 = self._gps_to_px(*self.drone_trail[i])
                c.create_line(x1,y1,x2,y2, fill=T["map_trail"], width=2)

        # WP'ler — turuncu diamond
        for idx,(wlat,wlon,lbl) in enumerate(self.waypoints):
            wx,wy = self._gps_to_px(wlat,wlon)
            c.create_polygon(wx,wy-8, wx+8,wy, wx,wy+8, wx-8,wy,
                             fill=T["map_wp"], outline="#fff", width=1)
            c.create_text(wx, wy-16, text=f"WP{idx+1}",
                          fill=T["map_wp"], font=(T["font_family"],8,"bold"))

        mode = self._map_mode.get()
        if mode == "TASK2":
            self._draw_task2_rect()
        else:
            self._draw_task1_fig8()

        # Drone simgesi
        if self.drone_lat is not None:
            self._wait_lbl.place_forget()
            dx,dy = self._gps_to_px(self.drone_lat, self.drone_lon)
            # Üçgen ok
            c.create_polygon(dx,dy-11, dx-7,dy+6, dx,dy+2, dx+7,dy+6,
                             fill=T["map_drone"], outline="#fff", width=1)
            # Koordinat etiketi — arka planlı
            c.create_rectangle(dx-52,dy+12, dx+52,dy+24,
                               fill=T["bg_card_dark"], outline=T["border"])
            c.create_text(dx,dy+18,
                          text=f"{self.drone_lat:.5f}, {self.drone_lon:.5f}",
                          fill=T["map_drone"], font=(T["font_family"],8))
        else:
            self._wait_lbl.place(relx=0.5, rely=0.5, anchor="center")

    def _draw_task2_rect(self):
        if len(self.waypoints) != 2: return
        (la1,lo1,_),(la2,lo2,_) = self.waypoints
        lamin,lamax = sorted([la1,la2]); lomin,lomax = sorted([lo1,lo2])
        x1,y1 = self._gps_to_px(lamax,lomin); x2,y2 = self._gps_to_px(lamin,lomax)
        # Noktalı kenarlık
        self._canvas.create_rectangle(x1,y1,x2,y2,
                                      outline=T["danger"], width=2, dash=(6,4))
        self._canvas.create_rectangle(x1,y1,x2,y1+18,
                                      fill=T["danger"], outline="")
        self._canvas.create_text((x1+x2)//2, y1+9,
                                 text="TASK 2  TARAMA ALANI",
                                 fill="#fff", font=(T["font_family"],8,"bold"))

    def _draw_task1_fig8(self):
        if len(self.waypoints) != 2: return
        pts = generate_task1_figure8_waypoints(self.waypoints, n_per_circle=8)
        if len(pts) < 3: return
        pxs = [self._gps_to_px(la,lo) for la,lo in pts]
        for i in range(1,len(pxs)):
            self._canvas.create_line(*pxs[i-1],*pxs[i],
                                     fill=T["accent_glow"], width=2, dash=(5,3))
        for (clat,clon,_),lbl in zip(self.waypoints[:2],["M1","M2"]):
            cx,cy = self._gps_to_px(clat,clon)
            self._canvas.create_oval(cx-5,cy-5,cx+5,cy+5,
                                     outline=T["accent_glow"], fill=T["map_bg"], width=2)
            self._canvas.create_text(cx,cy-14,text=lbl,
                                     fill=T["accent_glow"],font=(T["font_family"],8,"bold"))
        sx,sy = pxs[0]
        self._canvas.create_oval(sx-5,sy-5,sx+5,sy+5,
                                 fill=T["map_drone"],outline="#fff",width=2)
        self._canvas.create_text(sx,sy-14,text="S/E",
                                 fill=T["map_drone"],font=(T["font_family"],8,"bold"))

    # ---------- WP aksiyonları ----------
    def _add_wp(self):
        if self.drone_lat is None: return
        self.waypoints.append((self.drone_lat,self.drone_lon,str(len(self.waypoints)+1)))
        self._draww()

    def _clear_trail(self):
        self.drone_trail.clear(); self._draww()

    def _clear_wps(self):
        self.waypoints.clear(); self._draww()

    def _request_upload(self):
        try:    alt = float(self._alt_e.get() or "10")
        except: alt = 10.0
        try:    spc = max(2.0, float(self._spc_e.get() or "3"))
        except: spc = 3.0
        self._on_upload(waypoints=self.waypoints,
                        mode=self._map_mode.get(),
                        spacing_m=spc, alt=alt)
