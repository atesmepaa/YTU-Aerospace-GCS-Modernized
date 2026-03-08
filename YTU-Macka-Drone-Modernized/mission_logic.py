"""
mission_logic.py
~~~~~~~~~~~~~~~~
GPS matematiği ve waypoint üretim algoritmaları.
"""

import math


def _lat_lon_per_m(ref_lat: float):
    lat_per_m = 1.0 / 111320.0
    lon_per_m = 1.0 / (111320.0 * max(0.01, math.cos(math.radians(ref_lat))))
    return lat_per_m, lon_per_m


def _offset(clat, clon, bearing_rad, dist_m, ref_lat):
    lat_per_m, lon_per_m = _lat_lon_per_m(ref_lat)
    dlat = math.cos(bearing_rad) * dist_m * lat_per_m
    dlon = math.sin(bearing_rad) * dist_m * lon_per_m
    return clat + dlat, clon + dlon


def generate_task1_figure8_waypoints(waypoints: list, n_per_circle: int = 12, n_loops: int = 2) -> list:
    """
    Teknofest Görev 1 — Figure-8 (resme uygun):

    Waypoint sırası:
      waypoints[0] = Direk 1  (sağ direk)
      waypoints[1] = Direk 2  (sol direk)
      waypoints[2] = Pist     (kalkış/iniş noktası)

    Uçuş sırası (1 tur):
      Pist → Kesişim →
        Direk1 etrafında CW (saat yönü) →
      Kesişim →
        Direk2 etrafında CCW (ters saat) →
      Kesişim

    Tüm açılar tutarlı biçimde NAVİGASYON sisteminde (0=Kuzey, CW artar, radyan)
    hesaplanır — _offset() ile aynı sistem.

    Kesişim: Direk1-Direk2 orta noktası.
    Yarıçap: iki direk arası mesafenin yarısı.
    """
    if len(waypoints) < 3:
        return []

    (lat1, lon1, _) = waypoints[0]   # Direk 1
    (lat2, lon2, _) = waypoints[1]   # Direk 2
    (latp, lonp, _) = waypoints[2]   # Pist

    ref_lat = (lat1 + lat2) / 2.0
    lat_per_m = 1.0 / 111320.0
    lon_per_m = 1.0 / (111320.0 * max(0.01, math.cos(math.radians(ref_lat))))

    # Metre uzayına çevir (x=Doğu, y=Kuzey)
    def to_m(la, lo):
        return lo / lon_per_m, la / lat_per_m

    x1, y1 = to_m(lat1, lon1)
    x2, y2 = to_m(lat2, lon2)

    dx_m = x2 - x1
    dy_m = y2 - y1
    r_m  = math.sqrt(dx_m**2 + dy_m**2) / 2.0
    if r_m < 1.0:
        return []

    mid_lat = (lat1 + lat2) / 2.0
    mid_lon = (lon1 + lon2) / 2.0

    # NAV açısı: 0=Kuzey, CW artar
    # atan2 normal: (y, x) → math açısı (0=Doğu, CCW)
    # math → nav dönüşümü: nav = pi/2 - math_angle
    def nav_angle(dx, dy):
        """dx=Doğu bileşeni, dy=Kuzey bileşeni → navigasyon açısı (rad)"""
        return math.atan2(dx, dy)   # atan2(east, north) = nav açısı

    # Kesişimden Direk1'e ve Direk2'ye nav açıları
    # Direk1 kesişimin "axis ters" yönünde
    mid_to_d1_nav = nav_angle(x1 - (x1+x2)/2, y1 - (y1+y2)/2)
    mid_to_d2_nav = nav_angle(x2 - (x1+x2)/2, y2 - (y1+y2)/2)

    # Direk merkezinden kesişime bakan açı = 180° tersi
    d1_to_mid_nav = mid_to_d1_nav + math.pi
    d2_to_mid_nav = mid_to_d2_nav + math.pi

    def circle_pts(center_lat, center_lon, entry_nav_angle, clockwise: bool):
        """
        Tam çember waypoint listesi.
        entry_nav_angle: merkezden giriş noktasına bakılan nav açısı.
        CW: nav açısı artar (+), CCW: nav açısı azalır (-).
        """
        sign = +1 if clockwise else -1
        pts = []
        for i in range(1, n_per_circle + 1):
            a = entry_nav_angle + sign * (2 * math.pi * i / n_per_circle)
            pts.append(_offset(center_lat, center_lon, a, r_m, ref_lat))
        return pts

    def single_loop():
        pts = [(mid_lat, mid_lon)]
        # Direk1 CW
        pts.extend(circle_pts(lat1, lon1, d1_to_mid_nav, clockwise=True))
        pts.append((mid_lat, mid_lon))
        # Direk2 CCW
        pts.extend(circle_pts(lat2, lon2, d2_to_mid_nav, clockwise=False))
        pts.append((mid_lat, mid_lon))
        return pts

    # Pist → Kesişim yaklaşması
    all_pts = [(latp, lonp), (mid_lat, mid_lon)]

    for _ in range(max(1, n_loops)):
        loop = single_loop()
        all_pts.extend(loop[1:])   # kesişim zaten sonda var, tekrar ekleme

    return all_pts


def generate_task2_scan_waypoints(waypoints: list, spacing_m: float = 6.0) -> list:
    """
    Teknofest Görev 2 — Transit + Lawnmower tarama.

    waypoints[0], waypoints[1]: alana gidiş waypoint'leri (sırayla uçulur)
    waypoints[2], waypoints[3]: tarama alanının iki köşesi

    Döndürülen liste:
      [transit_wp1, transit_wp2, scan_pt1, scan_pt2, ..., scan_ptN]

    Pixhawk bunları AUTO modda sırayla gezer:
      → alana gider → lawnmower taramayı yapar.
    """
    if len(waypoints) != 4:
        return []

    (lat_t1, lon_t1, _) = waypoints[0]   # transit 1
    (lat_t2, lon_t2, _) = waypoints[1]   # transit 2
    (lat_c1, lon_c1, _) = waypoints[2]   # köşe 1
    (lat_c2, lon_c2, _) = waypoints[3]   # köşe 2

    lat_min, lat_max = sorted([lat_c1, lat_c2])
    lon_min, lon_max = sorted([lon_c1, lon_c2])
    lat_ref  = (lat_min + lat_max) / 2.0
    height_m = (lat_max - lat_min) * 111320.0
    width_m  = (lon_max - lon_min) * 111320.0 * max(0.2, math.cos(math.radians(lat_ref)))

    scan_pts = []
    if width_m >= height_m:
        n = max(2, int(height_m / spacing_m) + 1)
        for i in range(n):
            lat = lat_max - (i / (n - 1)) * (lat_max - lat_min)
            if i % 2 == 0:
                scan_pts += [(lat, lon_min), (lat, lon_max)]
            else:
                scan_pts += [(lat, lon_max), (lat, lon_min)]
    else:
        n = max(2, int(width_m / spacing_m) + 1)
        for i in range(n):
            lon = lon_min + (i / (n - 1)) * (lon_max - lon_min)
            if i % 2 == 0:
                scan_pts += [(lat_max, lon), (lat_min, lon)]
            else:
                scan_pts += [(lat_min, lon), (lat_max, lon)]

    cleaned_scan = []
    for p in scan_pts:
        if not cleaned_scan or abs(cleaned_scan[-1][0]-p[0]) > 1e-7 or abs(cleaned_scan[-1][1]-p[1]) > 1e-7:
            cleaned_scan.append(p)

    return [(lat_t1, lon_t1), (lat_t2, lon_t2)] + cleaned_scan


def waypoints_to_payload(waypoints: list, alt: float) -> list:
    return [{"lat": lat, "lon": lon, "alt": alt} for (lat, lon, _) in waypoints]

def pts_to_payload(pts: list, alt: float) -> list:
    return [{"lat": lat, "lon": lon, "alt": alt} for (lat, lon) in pts]
