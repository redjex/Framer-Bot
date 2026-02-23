# ── Telegram ──────────────────────────────────────────────────────────────────
PROXY_URL  = ""
API_TOKEN  = ""

# ── Пути ──────────────────────────────────────────────────────────────────────
FFMPEG_PATH = (
    r""
    r""
    r""
)

ANIMATION_DIR    = "animation"
HEART_ANIM_PATH  = f"{ANIMATION_DIR}/heard.webp"
LIKE_ANIM_PATH   = f"{ANIMATION_DIR}/like.webp"
DISLIKE_ANIM_PATH = f"{ANIMATION_DIR}/dislike.webp"

# ── MediaPipe ─────────────────────────────────────────────────────────────────
MP_MAX_HANDS            = 2
MP_DETECTION_CONFIDENCE = 0.7
MP_TRACKING_CONFIDENCE  = 0.7

# ── Детекция жестов ───────────────────────────────────────────────────────────
GESTURE_CONFIRM_FRAMES = 10   # кадров для подтверждения жеста
GESTURE_CLEAR_FRAMES   = 6    # пустых кадров для сброса
GESTURE_CONFIRM_RATIO  = 0.6  # доля кадров с жестом

ROTATION_ANGLES = [0, 90, 180, 270]  # углы перебора

# Landmarks для центрирования оверлея
LIKE_CENTER_IDS  = [0, 1, 2, 5, 9, 13, 17]
HEART_CENTER_IDS = [5, 6, 7, 8]

# ── Анимация ──────────────────────────────────────────────────────────────────
ANIM_FADE_FRAMES  = 15
OVERLAY_SIZE_MULT = 0.28   # доля от min(w, h)
OVERLAY_SIZE_ADD  = 40     # пиксели поверх
OVERLAY_SMOOTH    = 0.25   # коэффициент сглаживания позиции

# ── Видео / FFmpeg ────────────────────────────────────────────────────────────
DEFAULT_FPS        = 30.0
FFMPEG_PRESET      = "ultrafast"
FFMPEG_CRF         = 23
FRAME_QUEUE_SIZE   = 128   # ОПТИМИЗАЦИЯ: увеличено со 64 до 128 для снижения блокировок