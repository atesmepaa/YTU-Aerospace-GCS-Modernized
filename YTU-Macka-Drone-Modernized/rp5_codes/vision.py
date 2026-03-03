import cv2
import numpy as np
import threading
import time
import json
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer

from picamera2 import Picamera2

# =======================
# AYARLAR
# =======================
FRAME_W = 1280
FRAME_H = 720

STREAM_HOST = "0.0.0.0"
STREAM_PORT = 5005
STREAM_FPS  = 15

# Vision → Bridge UDP (aynı RPi üzerinde)
BRIDGE_UDP_HOST = "127.0.0.1"
BRIDGE_UDP_PORT = 14555

CENTER_THRESHOLD = 40   # px — hedef bu yarıçap içine girince drop

RED_LOWER1 = np.array([0,   120, 70])
RED_UPPER1 = np.array([10,  255, 255])
RED_LOWER2 = np.array([170, 120, 70])
RED_UPPER2 = np.array([180, 255, 255])

BLUE_LOWER = np.array([100, 120, 70])
BLUE_UPPER = np.array([130, 255, 255])

MIN_AREA        = 2000
DROP_COOLDOWN_S = 2.0

# Hedef merkeze kaç frame üst üste girerse drop
AIM_CONFIRM_FRAMES = 5


class VisionSystem:
    def __init__(self):
        # HQ Camera (libcamera) handle
        self.picam2 = None

        self.frame_lock  = threading.Lock()
        self.frame_bgr   = None

        self.jpeg_lock   = threading.Lock()
        self.latest_jpeg = None

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.stage_lock  = threading.Lock()
        self.stage       = "first"   # first → second → done
        self.last_drop_t = 0.0

        self._guided_active = False
        self._aim_counter   = 0

        self._stop = False

    # =======================
    # Bridge'e UDP gönder
    # =======================
    def _send_udp(self, msg: dict):
        try:
            self.sock.sendto(
                json.dumps(msg).encode("utf-8"),
                (BRIDGE_UDP_HOST, BRIDGE_UDP_PORT)
            )
        except Exception as e:
            print(f"[Vision] UDP send err: {e}")

    # =======================
    # Başlat
    # =======================
    def start(self):
        self._open_camera()
        threading.Thread(target=self._capture_loop, daemon=True).start()
        threading.Thread(target=self._detect_loop,  daemon=True).start()
        threading.Thread(target=self._mjpeg_server, daemon=True).start()
        print("[Vision] started (HQ Camera).")
        while not self._stop:
            time.sleep(1)

    # =======================
    # HQ Kamera (Picamera2)
    # =======================
    def _open_camera(self):
        self.picam2 = Picamera2()

        # Video config: düşük gecikme için main stream
        cfg = self.picam2.create_video_configuration(
            main={"size": (FRAME_W, FRAME_H), "format": "RGB888"},
            controls={
                # İstersen stabilize etmek için aç/kapat:
                # "AeEnable": True,
                # "AwbEnable": True,
            }
        )
        self.picam2.configure(cfg)

        # Bazı sistemlerde kamera oturması için kısa bekleme iyi gelir
        self.picam2.start()
        time.sleep(0.2)

        print(f"[Vision] HQ camera open {FRAME_W}x{FRAME_H}")

    def _capture_loop(self):
        """
        HQ kameradan frame oku.
        Picamera2 capture_array() RGB verir, OpenCV için BGR'ye çeviriyoruz.
        """
        while not self._stop:
            try:
                rgb = self.picam2.capture_array()
                # RGB -> BGR
                frame = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
                with self.frame_lock:
                    self.frame_bgr = frame
            except Exception as e:
                print(f"[Vision] capture err: {e}")
                time.sleep(0.05)

    # =======================
    # Tespit + Aim + Drop (~25 Hz)
    # =======================
    def _detect_loop(self):
        while not self._stop:
            with self.frame_lock:
                frame = None if self.frame_bgr is None else self.frame_bgr.copy()

            if frame is None:
                time.sleep(0.02)
                continue

            h, w = frame.shape[:2]
            cx0, cy0 = w // 2, h // 2
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

            with self.stage_lock:
                stage = self.stage

            target = None
            if stage == "first":
                target = self._detect_color_square(hsv, frame, "red")
            elif stage == "second":
                target = self._detect_color_square(hsv, frame, "blue")

            # HUD
            cv2.line(frame,   (cx0 - 20, cy0), (cx0 + 20, cy0), (0, 255, 0), 1)
            cv2.line(frame,   (cx0, cy0 - 20), (cx0, cy0 + 20), (0, 255, 0), 1)
            cv2.circle(frame, (cx0, cy0), CENTER_THRESHOLD, (0, 255, 255), 1)

            if target and stage != "done":
                dx, dy = target["dx"], target["dy"]

                if not self._guided_active:
                    self._guided_active = True
                    self._aim_counter   = 0
                    self._send_udp({"type": "vision_guided"})
                    print("[Vision] target found → request GUIDED")

                aim_str = f"AIM {self._aim_counter}/{AIM_CONFIRM_FRAMES}"
                cv2.putText(
                    frame,
                    f"STAGE={stage} offset=({dx:+d},{dy:+d}) {aim_str}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (255, 255, 255), 2,
                )

                if abs(dx) <= CENTER_THRESHOLD and abs(dy) <= CENTER_THRESHOLD:
                    self._aim_counter += 1
                    if self._aim_counter >= AIM_CONFIRM_FRAMES:
                        self._maybe_drop(stage)
                else:
                    self._aim_counter = 0

            elif stage != "done":
                if self._guided_active:
                    self._guided_active = False
                    self._aim_counter   = 0
                    self._send_udp({"type": "vision_lost"})
                    print("[Vision] target lost → request AUTO")

            ok, jpg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if ok:
                with self.jpeg_lock:
                    self.latest_jpeg = jpg.tobytes()

            time.sleep(0.04)  # ~25 Hz

    def _detect_color_square(self, hsv, frame, which: str):
        if which == "red":
            mask1 = cv2.inRange(hsv, RED_LOWER1, RED_UPPER1)
            mask2 = cv2.inRange(hsv, RED_LOWER2, RED_UPPER2)
            mask  = cv2.bitwise_or(mask1, mask2)
            draw_color = (0, 0, 255)
        else:
            mask       = cv2.inRange(hsv, BLUE_LOWER, BLUE_UPPER)
            draw_color = (255, 0, 0)

        kernel = np.ones((5, 5), np.uint8)
        mask   = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel)
        mask   = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        largest = max(contours, key=cv2.contourArea)
        area    = cv2.contourArea(largest)
        if area < MIN_AREA:
            return None

        peri   = cv2.arcLength(largest, True)
        approx = cv2.approxPolyDP(largest, 0.04 * peri, True)
        if len(approx) < 4 or len(approx) > 6:
            return None

        M = cv2.moments(largest)
        if M["m00"] == 0:
            return None

        cx   = int(M["m10"] / M["m00"])
        cy   = int(M["m01"] / M["m00"])
        h, w = frame.shape[:2]
        dx   = cx - (w // 2)
        dy   = cy - (h // 2)

        cv2.drawContours(frame, [approx], -1, draw_color, 3)
        cv2.circle(frame, (cx, cy), 6, draw_color, -1)

        return {"cx": cx, "cy": cy, "dx": dx, "dy": dy, "area": float(area)}

    def _maybe_drop(self, stage: str):
        now = time.time()
        if (now - self.last_drop_t) < DROP_COOLDOWN_S:
            return

        self._send_udp({"type": "vision_drop", "which": stage, "t": now})
        self.last_drop_t     = now
        self._aim_counter    = 0
        self._guided_active  = False

        with self.stage_lock:
            if stage == "first":
                self.stage = "second"
            elif stage == "second":
                self.stage = "done"

        print(f"[Vision] DROP SENT: {stage}")

    # =======================
    # MJPEG stream
    # =======================
    def _mjpeg_server(self):
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Age", "0")
                self.send_header("Cache-Control", "no-cache, private")
                self.send_header("Pragma", "no-cache")
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()

                while not parent._stop:
                    with parent.jpeg_lock:
                        jpg = parent.latest_jpeg
                    if jpg is None:
                        time.sleep(0.03)
                        continue
                    try:
                        self.wfile.write(b"--frame\r\n")
                        self.wfile.write(b"Content-Type: image/jpeg\r\n")
                        self.wfile.write(f"Content-Length: {len(jpg)}\r\n\r\n".encode("utf-8"))
                        self.wfile.write(jpg)
                        self.wfile.write(b"\r\n")
                        time.sleep(1.0 / max(1, STREAM_FPS))
                    except Exception:
                        break

            def log_message(self, fmt, *args):
                return

        httpd = HTTPServer((STREAM_HOST, STREAM_PORT), Handler)
        print(f"[Vision] MJPEG → http://0.0.0.0:{STREAM_PORT}/")
        httpd.serve_forever()


if __name__ == "__main__":
    VisionSystem().start()