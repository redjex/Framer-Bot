from __future__ import annotations

import cv2
import numpy as np
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from PIL import Image
from typing import TYPE_CHECKING

from config import ANIM_FADE_FRAMES, OVERLAY_SIZE_MULT, OVERLAY_SIZE_ADD, OVERLAY_SMOOTH


# ── Типы данных ───────────────────────────────────────────────────────────────

@dataclass(slots=True)
class AnimFrame:
    bgra:     np.ndarray
    duration: int   # миллисекунды


@dataclass
class WebpAnimation:
    frames: list[AnimFrame] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "WebpAnimation":
        anim = cls()
        try:
            img = Image.open(path)
            for i in range(getattr(img, "n_frames", 1)):
                img.seek(i)
                duration = img.info.get("duration", 50)
                rgba = img.convert("RGBA")
                bgra = cv2.cvtColor(np.array(rgba), cv2.COLOR_RGBA2BGRA)
                anim.frames.append(AnimFrame(bgra=bgra, duration=duration))
        except Exception as e:
            print(f"⚠️  Не удалось загрузить анимацию {path}: {e}")
        return anim

    def __bool__(self) -> bool:
        return bool(self.frames)


# ── Плеер ────────────────────────────────────────────────────────────────────

class PlayerState(Enum):
    IDLE    = auto()
    PLAYING = auto()
    FADING  = auto()


class AnimPlayer:
    def __init__(self, animation: WebpAnimation, fps: float = 30.0) -> None:
        self._anim     = animation
        self._frame_ms = 1000.0 / max(fps, 1.0)
        self.state     = PlayerState.IDLE
        self._idx      = 0
        self._ms_accum = 0.0
        self._fade_cnt = 0
        self.alpha     = 1.0

    def update(self, active: bool) -> float:
        if not self._anim:
            return 0.0

        if active:
            if self.state == PlayerState.IDLE:
                self._idx = 0
                self._ms_accum = 0.0
            self.state     = PlayerState.PLAYING
            self._fade_cnt = 0
            self.alpha     = 1.0

        if self.state == PlayerState.PLAYING:
            self._advance()
            return self.alpha

        if self.state == PlayerState.FADING:
            self._fade_cnt += 1
            self.alpha = max(0.0, 1.0 - self._fade_cnt / ANIM_FADE_FRAMES)
            if self.alpha <= 0.0:
                self.state = PlayerState.IDLE
            return self.alpha

        return 0.0

    def start_fade(self) -> None:
        if self.state == PlayerState.PLAYING:
            self.state     = PlayerState.FADING
            self._fade_cnt = 0
            self.alpha     = 1.0

    @property
    def current_frame(self) -> np.ndarray | None:
        if not self._anim:
            return None
        return self._anim.frames[self._idx].bgra

    @property
    def is_active(self) -> bool:
        return self.state != PlayerState.IDLE

    def _advance(self) -> None:
        self._ms_accum += self._frame_ms
        while True:
            dur = self._anim.frames[self._idx].duration
            if self._ms_accum < dur:
                break
            self._ms_accum -= dur
            self._idx = (self._idx + 1) % len(self._anim.frames)


# ── Кеш ресайза ───────────────────────────────────────────────────────────────

class _ResizeCache:
    """
    ОПТИМИЗАЦИЯ 1: Кешируем результат cv2.resize для каждого кадра анимации.
    При одном размере оверлея (size не меняется) resize происходит 
    ровно n_frames раз за всё видео, а не n_frames * n_video_frames раз.
    Также предвычисляем alpha-канал как float32 для быстрого blending.
    """

    def __init__(self) -> None:
        # key: (id(bgra_array), size) → (bgr_f32, alpha_f32_3ch)
        self._cache: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}

    def get(self, bgra: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray]:
        key = (id(bgra), size)
        if key not in self._cache:
            s = max(2, size)
            resized = cv2.resize(bgra, (s, s), interpolation=cv2.INTER_LINEAR)
            b, g, r, a = cv2.split(resized)
            # Предвычисляем alpha как float32 [0..1]
            alpha_f = a.astype(np.float32) * (1.0 / 255.0)
            alpha3  = alpha_f[:, :, np.newaxis]
            bgr_f   = np.stack([b, g, r], axis=2).astype(np.float32)
            self._cache[key] = (bgr_f, alpha3)
        return self._cache[key]


_resize_cache = _ResizeCache()


# ── Рендеринг оверлея ─────────────────────────────────────────────────────────

def overlay_bgra(
    bg: np.ndarray,
    anim_bgra: np.ndarray,
    cx: int,
    cy: int,
    size: int,
    alpha_mul: float = 1.0,
) -> None:
    """
    ОПТИМИЗАЦИЯ 2: Используем предвычисленный (кешированный) bgr_f32 и alpha3.
    При alpha_mul == 1.0 избегаем умножения alpha на скаляр.
    ОПТИМИЗАЦИЯ 3: np.clip + astype вместо промежуточного float-буфера.
    """
    h_bg, w_bg = bg.shape[:2]
    s = max(2, size)

    bgr_f, alpha3 = _resize_cache.get(anim_bgra, s)

    x1, y1 = cx - s // 2, cy - s // 2
    x2, y2 = x1 + s, y1 + s

    sx1 = max(0, -x1);  sy1 = max(0, -y1)
    ex1 = max(0,  x1);  ey1 = max(0,  y1)
    ex2 = min(w_bg, x2); ey2 = min(h_bg, y2)
    sx2 = sx1 + (ex2 - ex1)
    sy2 = sy1 + (ey2 - ey1)

    if ex2 <= ex1 or ey2 <= ey1:
        return

    roi      = bg[ey1:ey2, ex1:ex2].astype(np.float32)
    anim_bgr = bgr_f[sy1:sy2, sx1:sx2]
    alpha    = alpha3[sy1:sy2, sx1:sx2]

    if alpha_mul != 1.0:
        alpha = alpha * alpha_mul

    # ОПТИМИЗАЦИЯ 4: Используем cv2.addWeighted-стиль через fused multiply-add
    # np.add(a, b, out=buf) быстрее чем a + b (нет промежуточного буфера)
    inv_alpha = 1.0 - alpha
    blended = anim_bgr * alpha + roi * inv_alpha
    np.clip(blended, 0, 255, out=blended)
    bg[ey1:ey2, ex1:ex2] = blended.astype(np.uint8)


# ── Высокоуровневый фасад ─────────────────────────────────────────────────────

class AnimOverlay:
    def __init__(
        self,
        animations: dict[str, WebpAnimation],
        fps: float = 30.0,
        frame_w: int = 0,
        frame_h: int = 0,
    ) -> None:
        self._players: dict[str, AnimPlayer] = {
            name: AnimPlayer(anim, fps)
            for name, anim in animations.items()
        }
        self._frame_w   = frame_w
        self._frame_h   = frame_h
        self._size      = int(min(frame_w, frame_h) * OVERLAY_SIZE_MULT) + OVERLAY_SIZE_ADD
        self._smooth    = OVERLAY_SMOOTH
        self._curr_cx: int | None = None
        self._curr_cy: int | None = None
        # ОПТИМИЗАЦИЯ 5: Предвычисляем смещения как int
        self._offsets = {"heart": 20, "like": 0, "dislike": 0}

    def update_and_draw(
        self,
        frame: np.ndarray,
        confirmed_gesture: str | None,
        raw_gesture: str | None,
        last_nx: float,
        last_ny: float,
    ) -> None:
        # Fade тех анимаций, сырой сигнал которых пропал
        for name, player in self._players.items():
            if player.state == PlayerState.PLAYING and raw_gesture != name:
                player.start_fade()

        # Обновить альфы
        alphas: dict[str, float] = {
            name: player.update(confirmed_gesture == name)
            for name, player in self._players.items()
        }

        # Позиция центра оверлея
        any_active = any(p.is_active for p in self._players.values())
        if any_active:
            tx = int(last_nx * self._frame_w)
            ty = int(last_ny * self._frame_h)
            if self._curr_cx is None:
                self._curr_cx, self._curr_cy = tx, ty
            else:
                k = self._smooth
                self._curr_cx = int(self._curr_cx + (tx - self._curr_cx) * k)
                self._curr_cy = int(self._curr_cy + (ty - self._curr_cy) * k)
        else:
            self._curr_cx = self._curr_cy = None

        if self._curr_cx is None:
            return

        cx, cy = self._curr_cx, self._curr_cy

        for name, alpha in alphas.items():
            if alpha <= 0.0:
                continue
            bgra = self._players[name].current_frame
            if bgra is not None:
                oy = self._offsets.get(name, 0)
                overlay_bgra(frame, bgra, cx, cy + oy, self._size, alpha_mul=alpha)