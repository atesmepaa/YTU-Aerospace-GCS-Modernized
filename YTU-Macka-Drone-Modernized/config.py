# =======================
# AYARLAR
# =======================

SIK_PORT = "COM6"
SIK_BAUD = 57600

RPI_STREAM_URL = "http://192.168.1.232:5005"
CAM_DISPLAY_SIZE = (480, 270)

MOUSE_IMU_ENABLED = False
DEBUG_SIK_RX    = False
DEBUG_JSON_FAIL = True

ARDU_MODE_DISPLAY = {
    "STABILIZE": "STABİLİZE",
    "ACRO":      "ACRO",
    "ALT_HOLD":  "İRTİFA TUT",
    "AUTO":      "OTO GÖREV",
    "GUIDED":    "GUIDED",
    "LOITER":    "LOITER (BEKLE)",
    "RTL":       "BAŞA DÖN (RTL)",
    "CIRCLE":    "DAİRE",
    "LAND":      "İNİŞ",
    "DRIFT":     "DRIFT",
    "SPORT":     "SPOR",
    "AUTOTUNE":  "OTO AYAR",
    "POSHOLD":   "POZİSYON TUT",
    "BRAKE":     "FREN",
    "THROW":     "FIRLATMA",
    "SMART_RTL": "SMART RTL",
    "FOLLOW":    "TAKİP",
    "AUTO_RTL":  "OTO RTL",
}

# =======================
# TEMA
# =======================
THEME = {
    # Ana renkler
    "bg_root":        "#0d0d0d",
    "bg_panel":       "#111111",
    "bg_card":        "#181818",
    "bg_card_dark":   "#0f0f0f",

    # Vurgu — turuncu
    "accent":         "#FF6B1A",
    "accent_dim":     "#cc5514",
    "accent_glow":    "#ff8c42",

    # Metin
    "text_primary":   "#F0F0F0",
    "text_secondary": "#888888",
    "text_accent":    "#FF6B1A",

    # Durum renkleri
    "ok":     "#00e676",
    "warn":   "#ffab00",
    "danger": "#ff1744",

    # Kenarlık / ayraç
    "border": "#2a2a2a",

    # Harita
    "map_bg":   "#0a0a14",
    "map_grid": "#1a1a2e",
    "map_trail":"#00aaff",
    "map_wp":   "#FF6B1A",
    "map_drone":"#00e676",

    # Font
    "font_family": "Consolas",
}