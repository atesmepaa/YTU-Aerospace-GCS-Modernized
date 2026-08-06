# =======================
# AYARLAR
# =======================

SIK_PORT = "COM6"
SIK_BAUD = 57600

RPI_STREAM_URL = "http://192.168.1.232:5005"
CAM_DISPLAY_SIZE = (480, 270)

# MOUSE IMU SİMÜLATÖRÜ — gerçek donanımda False yap
MOUSE_IMU_ENABLED = False

# Debug ayarları
DEBUG_SIK_RX   = False   # True yaparsan çok log basar
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