from __future__ import annotations

import queue
import subprocess
import threading
from pathlib import Path

import cv2
import numpy as np

from animation import AnimOverlay, WebpAnimation
from config import (
    DEFAULT_FPS,
    FFMPEG_PATH,
    FFMPEG_PRESET,
    FFMPEG_CRF,
    FRAME_QUEUE_SIZE,
    HEART_ANIM_PATH,
    LIKE_ANIM_PATH,
    DISLIKE_ANIM_PATH,
)
from gesture_buffer import GestureBuffer
from gesture_detector import GestureDetector
from user_storage import get_all_paths

_STOP = object()

# ── Ватермарка ────────────────────────────────────────────────────────────────

_WATERMARK_TEXT = "@framer_robot"

# Путь к шрифту Special Gothic Expanded Regular.
# Положи файл .ttf рядом с main.py под именем SpecialGothicExpanded.ttf
_FONT_PATH = Path(__file__).parent / "SpecialGothicExpanded.ttf"


def _draw_watermark(frame: np.ndarray) -> None:
    """
    Рисует @framer_robot снизу по центру кружка.
    Шрифт: Special Gothic Expanded Regular (TTF рядом с проектом).
    Белый текст, прозрачность ~0.5.

    Для настройки позиции меняй:
        y = int(h * 0.93)   ← вертикаль (0.0 = верх, 1.0 = низ)
        x = (w - tw) // 2  ← горизонталь (центр)
    Для настройки прозрачности меняй:
        cv2.addWeighted(overlay, 0.5, ...)  ← первое 0.5 = непрозрачность текста
    Для настройки размера шрифта меняй:
        font_size = max(10, int(min(w, h) * 0.055))
    """
    from PIL import Image, ImageDraw, ImageFont

    h, w = frame.shape[:2]

    # ── Параметры (меняй здесь) ───────────────────────────────────────────────
    font_size   = max(10, int(min(w, h) * 0.045))   # размер шрифта
    alpha_level = 0.5                                # прозрачность 0.0–1.0
    y_pos       = int(h * 0.93)                     # вертикальная позиция
    # ─────────────────────────────────────────────────────────────────────────

    try:
        pil_font = ImageFont.truetype(str(_FONT_PATH), font_size)
    except Exception as e:
        print(f"⚠️  Шрифт не найден ({_FONT_PATH}): {e}\n"
              f"   Положи SpecialGothicExpanded.ttf рядом с main.py")
        return

    # Конвертируем кадр BGR → RGB для PIL
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)

    # Рисуем текст на отдельном слое для прозрачности
    overlay_pil = pil_img.copy()
    draw = ImageDraw.Draw(overlay_pil)

    bbox = draw.textbbox((0, 0), _WATERMARK_TEXT, font=pil_font)
    tw = bbox[2] - bbox[0]

    x = (w - tw) // 2  # горизонтальный центр
    y = y_pos - (bbox[3] - bbox[1])  # выравниваем нижний край по y_pos

    draw.text((x, y), _WATERMARK_TEXT, font=pil_font, fill=(255, 255, 255))

    # Смешиваем: overlay * alpha + original * (1 - alpha)
    blended = Image.blend(pil_img, overlay_pil, alpha_level)

    # Конвертируем обратно в BGR и записываем в frame inplace
    result = cv2.cvtColor(np.array(blended), cv2.COLOR_RGB2BGR)
    np.copyto(frame, result)


# ОПТИМИЗАЦИЯ 1: Сколько кадров пропускать детекцию когда жест стабильно подтверждён.
# Жест в буфере уже confirmed — незачем вызывать MediaPipe каждый кадр.
# Значение 2 означает: один из каждых 3 кадров проверяем, не пропал ли жест.
_DETECTION_SKIP_AFTER_CONFIRM = 2


def _load_animations(user_id: int | None = None) -> dict[str, WebpAnimation]:
    if user_id is not None:
        paths = get_all_paths(user_id)
    else:
        paths = {
            "heart":   HEART_ANIM_PATH,
            "like":    LIKE_ANIM_PATH,
            "dislike": DISLIKE_ANIM_PATH,
        }
    return {name: WebpAnimation.load(path) for name, path in paths.items()}


def _build_ffmpeg_cmd(
    frame_w: int,
    frame_h: int,
    fps: float,
    input_path: str,
    output_path: str,
) -> list[str]:
    return [
        FFMPEG_PATH, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{frame_w}x{frame_h}",
        "-r", str(fps),
        "-i", "pipe:0",
        "-i", input_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-vcodec", "libx264", "-preset", FFMPEG_PRESET, "-crf", str(FFMPEG_CRF),
        "-pix_fmt", "yuv420p", "-acodec", "copy", "-shortest",
        output_path,
    ]


# ── Потоки ────────────────────────────────────────────────────────────────────

def _reader_thread(cap: cv2.VideoCapture, raw_q: queue.Queue) -> None:
    try:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            raw_q.put(frame)
    finally:
        raw_q.put(_STOP)


def _processor_thread(
    raw_q: queue.Queue,
    out_q: queue.Queue,
    detector: GestureDetector,
    overlay: AnimOverlay,
    buf: GestureBuffer,
    is_business: bool = False,
) -> None:
    """
    ОПТИМИЗАЦИЯ 2: Adaptive detection skip.
    Когда жест уже стабильно подтверждён, пропускаем MediaPipe inference 
    на N кадрах из N+1. Детектор всё равно вызывается регулярно чтобы 
    вовремя заметить исчезновение жеста.
    Пропускать кадры detection безопасно т.к. GestureBuffer требует 
    GESTURE_CLEAR_FRAMES подряд пустых — один пропущенный кадр не сбросит.
    """
    skip_counter = 0
    last_raw_g: str | None = None  # последний реальный raw жест

    try:
        while True:
            item = raw_q.get()
            if item is _STOP:
                break

            frame: np.ndarray = item

            # Пропускаем inference если жест стабилен
            should_detect = True
            if buf.confirmed is not None and _DETECTION_SKIP_AFTER_CONFIRM > 0:
                if skip_counter < _DETECTION_SKIP_AFTER_CONFIRM:
                    should_detect = False
                    skip_counter += 1
                else:
                    skip_counter = 0

            if should_detect:
                raw_g, raw_nx, raw_ny = detector.process_frame(
                    frame, skip_rotation=buf.skip_rotation
                )
                last_raw_g = raw_g
                confirmed = buf.push(raw_g, raw_nx, raw_ny)
            else:
                confirmed = buf.confirmed

            overlay.update_and_draw(
                frame,
                confirmed_gesture=confirmed,
                raw_gesture=last_raw_g,   # ВАЖНО: реальный raw сигнал, а не confirmed
                last_nx=buf.last_nx,
                last_ny=buf.last_ny,
            )

            if not is_business:
                _draw_watermark(frame)

            out_q.put(frame)
    finally:
        out_q.put(_STOP)


def _writer_thread(out_q: queue.Queue, stdin) -> None:
    """
    ОПТИМИЗАЦИЯ 3: Запись в stdin FFmpeg в отдельном потоке.
    Теперь processor не ждёт пока FFmpeg примет данные —
    он сразу берёт следующий кадр из raw_q.
    Используем bytearray-буфер для снижения количества системных вызовов write.
    """
    WRITE_BATCH = 4  # кадров на один flush
    batch: list[bytes] = []

    try:
        while True:
            item = out_q.get()
            if item is _STOP:
                break
            batch.append(item.tobytes())
            if len(batch) >= WRITE_BATCH:
                stdin.write(b"".join(batch))
                batch.clear()
    finally:
        if batch:
            stdin.write(b"".join(batch))


# ── Публичный API ─────────────────────────────────────────────────────────────

def process_video(input_path: str, output_path: str, user_id: int | None = None, is_business: bool = False) -> None:
    cap     = cv2.VideoCapture(input_path)
    fps     = cap.get(cv2.CAP_PROP_FPS) or DEFAULT_FPS
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    animations = _load_animations(user_id)
    overlay    = AnimOverlay(animations, fps=fps, frame_w=frame_w, frame_h=frame_h)
    detector   = GestureDetector()
    buf        = GestureBuffer()

    ffmpeg_cmd = _build_ffmpeg_cmd(frame_w, frame_h, fps, input_path, output_path)
    proc = subprocess.Popen(
        ffmpeg_cmd,
        stdin=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    # ОПТИМИЗАЦИЯ 4: Увеличиваем очереди — меньше блокировок между потоками
    raw_q: queue.Queue = queue.Queue(maxsize=max(FRAME_QUEUE_SIZE, 128))
    out_q: queue.Queue = queue.Queue(maxsize=max(FRAME_QUEUE_SIZE, 128))

    t_reader    = threading.Thread(target=_reader_thread,    args=(cap, raw_q),                          daemon=True)
    t_processor = threading.Thread(target=_processor_thread, args=(raw_q, out_q, detector, overlay, buf, is_business), daemon=True)
    t_writer    = threading.Thread(target=_writer_thread,    args=(out_q, proc.stdin),                   daemon=True)

    try:
        t_reader.start()
        t_processor.start()
        t_writer.start()
        # Ждём завершения writer (он последний в цепочке)
        t_writer.join()
    except Exception as e:
        print(f"❌ Ошибка обработки видео: {e}")
    finally:
        t_reader.join(timeout=10)
        t_processor.join(timeout=10)
        t_writer.join(timeout=5)
        cap.release()
        detector.close()
        proc.stdin.close()
        proc.wait()