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


def generate_task1_figure8_waypoints(waypoints: list, n_per_circle: int = 8) -> list:
    if len(waypoints) < 2:
        return []
    (lat1, lon1, _), (lat2, lon2, _) = waypoints[0], waypoints[1]
    ref_lat = (lat1 + lat2) / 2.0
    dlat_m  = (lat2 - lat1) * 111320.0
    dlon_m  = (lon2 - lon1) * 111320.0 * math.cos(math.radians(ref_lat))
    r_m     = math.sqrt(dlat_m**2 + dlon_m**2) / 2.0
    if r_m < 1.0:
        return []
    angle_rad = math.atan2(dlon_m, dlat_m)
    mid_lat, mid_lon = (lat1+lat2)/2.0, (lon1+lon2)/2.0
    pts = [(mid_lat, mid_lon)]
    for i in range(1, n_per_circle+1):
        a = angle_rad + (2*math.pi*i/n_per_circle)
        pts.append(_offset(lat1, lon1, a, r_m, ref_lat))
    pts.append((mid_lat, mid_lon))
    for i in range(1, n_per_circle+1):
        a = (angle_rad+math.pi) - (2*math.pi*i/n_per_circle)
        pts.append(_offset(lat2, lon2, a, r_m, ref_lat))
    pts.append((mid_lat, mid_lon))
    return pts


def generate_task2_scan_waypoints(waypoints: list, spacing_m: float = 6.0) -> list:
    if len(waypoints) != 2:
        return []
    (lat1, lon1, _), (lat2, lon2, _) = waypoints
    lat_min, lat_max = sorted([lat1, lat2])
    lon_min, lon_max = sorted([lon1, lon2])
    lat_ref  = (lat_min + lat_max) / 2.0
    height_m = (lat_max - lat_min) * 111320.0
    width_m  = (lon_max - lon_min) * 111320.0 * max(0.2, math.cos(math.radians(lat_ref)))
    pts = []
    if width_m >= height_m:
        n = max(2, int(height_m // spacing_m) + 1)
        for i in range(n):
            lat = lat_max - (i/(n-1))*(lat_max-lat_min)
            pts += [(lat, lon_min), (lat, lon_max)] if i%2==0 else [(lat, lon_max), (lat, lon_min)]
    else:
        n = max(2, int(width_m // spacing_m) + 1)
        for i in range(n):
            lon = lon_min + (i/(n-1))*(lon_max-lon_min)
            pts += [(lat_min, lon), (lat_max, lon)] if i%2==0 else [(lat_max, lon), (lat_min, lon)]
    cleaned = []
    for p in pts:
        if not cleaned or abs(cleaned[-1][0]-p[0])>1e-7 or abs(cleaned[-1][1]-p[1])>1e-7:
            cleaned.append(p)
    return cleaned


def waypoints_to_payload(waypoints: list, alt: float) -> list:
    return [{"lat": lat, "lon": lon, "alt": alt} for (lat, lon, _) in waypoints]

def pts_to_payload(pts: list, alt: float) -> list:
    return [{"lat": lat, "lon": lon, "alt": alt} for (lat, lon) in pts]