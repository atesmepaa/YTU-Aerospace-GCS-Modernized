"""
mission_logic.py
~~~~~~~~~~~~~~~~
GPS matematiği ve waypoint üretim algoritmaları.
Tkinter / UI bağımlılığı yoktur — bağımsız test edilebilir.
"""

import math


# ------------------------------------------------------------------
# GPS yardımcı fonksiyonları
# ------------------------------------------------------------------

def meters_to_latlon(lat_deg: float, dx_m: float, dy_m: float):
    """
    dx_m: doğu (+) / batı (-) metre
    dy_m: kuzey (+) / güney (-) metre
    """
    lat_rad = math.radians(lat_deg)
    dlat = dy_m / 111320.0
    dlon = dx_m / (111320.0 * max(0.2, math.cos(lat_rad)))
    return dlat, dlon


def _lat_lon_per_m(ref_lat: float):
    """1 metreye karşılık gelen derece miktarlarını döner."""
    lat_per_m = 1.0 / 111320.0
    lon_per_m = 1.0 / (111320.0 * max(0.01, math.cos(math.radians(ref_lat))))
    return lat_per_m, lon_per_m


def _offset(clat: float, clon: float, bearing_rad: float, dist_m: float, ref_lat: float):
    """
    bearing_rad: kuzeyden saat yönüne açı
    dist_m     : metre cinsinden uzaklık
    """
    lat_per_m, lon_per_m = _lat_lon_per_m(ref_lat)
    dlat = math.cos(bearing_rad) * dist_m * lat_per_m
    dlon = math.sin(bearing_rad) * dist_m * lon_per_m
    return clat + dlat, clon + dlon


# ------------------------------------------------------------------
# Task 1 — Figure-8
# ------------------------------------------------------------------

def generate_task1_figure8_waypoints(waypoints: list, n_per_circle: int = 8) -> list:
    """
    2 WP = iki dairenin merkezi.
    Yarıçap = iki merkez arası mesafenin yarısı.
    1. daire saat yönünde, 2. daire saat yönünün tersine üretilir.
    Başlangıç/bitiş: iki dairenin orta noktası (kesişim).

    Returns:
        [(lat, lon), ...] — boş liste döner, oluşturulamazsa
    """
    if len(waypoints) < 2:
        return []

    (lat1, lon1, _), (lat2, lon2, _) = waypoints[0], waypoints[1]
    ref_lat = (lat1 + lat2) / 2.0

    dlat_m = (lat2 - lat1) * 111320.0
    dlon_m = (lon2 - lon1) * 111320.0 * math.cos(math.radians(ref_lat))
    dist_m = math.sqrt(dlat_m ** 2 + dlon_m ** 2)
    r_m    = dist_m / 2.0

    if r_m < 1.0:
        return []

    angle_rad = math.atan2(dlon_m, dlat_m)  # kuzey=0, doğu=+90

    mid_lat = (lat1 + lat2) / 2.0
    mid_lon = (lon1 + lon2) / 2.0

    pts = [(mid_lat, mid_lon)]

    # 1. Daire — saat yönünde
    for i in range(1, n_per_circle + 1):
        a = angle_rad + (2 * math.pi * i / n_per_circle)
        pts.append(_offset(lat1, lon1, a, r_m, ref_lat))

    pts.append((mid_lat, mid_lon))

    # 2. Daire — saat yönünün tersine
    for i in range(1, n_per_circle + 1):
        a = (angle_rad + math.pi) - (2 * math.pi * i / n_per_circle)
        pts.append(_offset(lat2, lon2, a, r_m, ref_lat))

    pts.append((mid_lat, mid_lon))
    return pts


# ------------------------------------------------------------------
# Task 2 — Tarama (zigzag)
# ------------------------------------------------------------------

def generate_task2_scan_waypoints(waypoints: list, spacing_m: float = 6.0) -> list:
    """
    Tam 2 WP içeren waypoints listesinden dikdörtgen alanı tarayan
    zigzag (lat, lon) tuple listesi üretir.

    Returns:
        [(lat, lon), ...] — boş liste döner, oluşturulamazsa
    """
    if len(waypoints) != 2:
        return []

    (lat1, lon1, _), (lat2, lon2, _) = waypoints
    lat_min, lat_max = (lat1, lat2) if lat1 < lat2 else (lat2, lat1)
    lon_min, lon_max = (lon1, lon2) if lon1 < lon2 else (lon2, lon1)

    lat_ref  = (lat_min + lat_max) / 2.0
    height_m = (lat_max - lat_min) * 111320.0
    width_m  = (lon_max - lon_min) * 111320.0 * max(0.2, math.cos(math.radians(lat_ref)))

    pts = []
    sweep_along_lon = width_m >= height_m

    if sweep_along_lon:
        n_lanes = max(2, int(height_m // spacing_m) + 1)
        for i in range(n_lanes):
            frac = i / (n_lanes - 1)
            lat  = lat_max - frac * (lat_max - lat_min)
            if i % 2 == 0:
                pts += [(lat, lon_min), (lat, lon_max)]
            else:
                pts += [(lat, lon_max), (lat, lon_min)]
    else:
        n_lanes = max(2, int(width_m // spacing_m) + 1)
        for i in range(n_lanes):
            frac = i / (n_lanes - 1)
            lon  = lon_min + frac * (lon_max - lon_min)
            if i % 2 == 0:
                pts += [(lat_min, lon), (lat_max, lon)]
            else:
                pts += [(lat_max, lon), (lat_min, lon)]

    cleaned = []
    for p in pts:
        if not cleaned or (
            abs(cleaned[-1][0] - p[0]) > 1e-7 or
            abs(cleaned[-1][1] - p[1]) > 1e-7
        ):
            cleaned.append(p)

    return cleaned


# ------------------------------------------------------------------
# Waypoint listesini JSON payload'a çevir
# ------------------------------------------------------------------

def waypoints_to_payload(waypoints: list, alt: float) -> list:
    """[(lat, lon, label), ...] → [{"lat":..., "lon":..., "alt":...}, ...]"""
    return [{"lat": lat, "lon": lon, "alt": alt} for (lat, lon, _) in waypoints]


def pts_to_payload(pts: list, alt: float) -> list:
    """[(lat, lon), ...] → [{"lat":..., "lon":..., "alt":...}, ...]"""
    return [{"lat": lat, "lon": lon, "alt": alt} for (lat, lon) in pts]