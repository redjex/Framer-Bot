from __future__ import annotations

from collections import deque

from config import (
    GESTURE_CONFIRM_FRAMES,
    GESTURE_CLEAR_FRAMES,
    GESTURE_CONFIRM_RATIO,
)

GestureName = str | None


class GestureBuffer:
    def __init__(self) -> None:
        self._buf:           deque[GestureName] = deque(maxlen=GESTURE_CONFIRM_FRAMES)
        self._no_hand_streak: int  = 0
        self.confirmed:       GestureName = None
        self.skip_rotation:   bool        = False

        # Последняя нормализованная позиция руки
        self.last_nx: float = 0.5
        self.last_ny: float = 0.5

    def push(self, raw_gesture: GestureName, nx: float | None, ny: float | None) -> GestureName:
        """
        Добавить результат детекции одного кадра.
        Возвращает текущий confirmed жест (может быть None).
        """
        if raw_gesture is None:
            self._no_hand_streak += 1
        else:
            self._no_hand_streak = 0
            if nx is not None:
                self.last_nx, self.last_ny = nx, ny  # type: ignore[assignment]

        self._buf.append(raw_gesture)

        new_confirmed = self._get_confirmed()
        if new_confirmed is not None:
            self.confirmed    = new_confirmed
            self.skip_rotation = True
        elif self._no_hand_streak >= GESTURE_CLEAR_FRAMES:
            self.confirmed     = None
            self.skip_rotation = False

        return self.confirmed

    def _get_confirmed(self) -> GestureName:
        if len(self._buf) < GESTURE_CONFIRM_FRAMES:
            return None

        counts: dict[str, int] = {}
        for g in self._buf:
            if g is not None:
                counts[g] = counts.get(g, 0) + 1

        if not counts:
            return None

        best = max(counts, key=counts.__getitem__)
        if counts[best] / len(self._buf) >= GESTURE_CONFIRM_RATIO:
            return best
        return None