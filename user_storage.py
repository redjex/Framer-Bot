from __future__ import annotations

import json
import os
from pathlib import Path

from config import HEART_ANIM_PATH, LIKE_ANIM_PATH, DISLIKE_ANIM_PATH

USER_ANIM_DIR = Path("user_animations")

# Дефолтные пути из конфига
_DEFAULTS: dict[str, str] = {
    "heart":   HEART_ANIM_PATH,
    "like":    LIKE_ANIM_PATH,
    "dislike": DISLIKE_ANIM_PATH,
}

GESTURE_NAMES = list(_DEFAULTS.keys())


def _user_dir(user_id: int) -> Path:
    p = USER_ANIM_DIR / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _emoji_ids_path(user_id: int) -> Path:
    return _user_dir(user_id) / "emoji_ids.json"


def _load_emoji_ids(user_id: int) -> dict[str, str]:
    """Загружает словарь gesture → custom_emoji_id из JSON."""
    path = _emoji_ids_path(user_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_emoji_ids(user_id: int, data: dict[str, str]) -> None:
    """Сохраняет словарь gesture → custom_emoji_id в JSON."""
    _emoji_ids_path(user_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── Кастомные emoji ID ────────────────────────────────────────────────────────

def set_custom_emoji_id(user_id: int, gesture: str, emoji_id: str) -> None:
    """Сохраняет custom_emoji_id для жеста пользователя."""
    data = _load_emoji_ids(user_id)
    data[gesture] = emoji_id
    _save_emoji_ids(user_id, data)
    print(f"✅ Сохранён emoji_id для {gesture}: {emoji_id}")


def get_custom_emoji_id(user_id: int, gesture: str) -> str | None:
    """Возвращает сохранённый custom_emoji_id или None если нет."""
    return _load_emoji_ids(user_id).get(gesture)


def clear_custom_emoji_id(user_id: int, gesture: str) -> None:
    """Удаляет сохранённый custom_emoji_id для жеста."""
    data = _load_emoji_ids(user_id)
    if gesture in data:
        del data[gesture]
        _save_emoji_ids(user_id, data)


# ── Анимации ──────────────────────────────────────────────────────────────────

def get_animation_path(user_id: int, gesture: str) -> str:
    """Возвращает путь к анимации для данного пользователя и жеста.
    Если кастомной нет — возвращает дефолтную."""
    custom = _user_dir(user_id) / f"{gesture}.webp"
    if custom.exists():
        return str(custom)
    return _DEFAULTS[gesture]


def set_animation_path(user_id: int, gesture: str, webp_data: bytes) -> str:
    """Сохраняет WebP-данные как кастомную анимацию пользователя.
    Возвращает путь к сохранённому файлу."""
    path = _user_dir(user_id) / f"{gesture}.webp"
    path.write_bytes(webp_data)
    print(f"✅ Сохранена кастомная анимация: {path}")
    return str(path)


def reset_animation(user_id: int, gesture: str) -> bool:
    """Удаляет кастомную анимацию и emoji_id (возврат к дефолтной).
    Возвращает True если файл существовал."""
    path = _user_dir(user_id) / f"{gesture}.webp"
    existed = path.exists()
    if existed:
        path.unlink()
    # Всегда чистим emoji_id при сбросе
    clear_custom_emoji_id(user_id, gesture)
    return existed


def has_custom_animation(user_id: int, gesture: str) -> bool:
    return (_user_dir(user_id) / f"{gesture}.webp").exists()


def get_all_paths(user_id: int) -> dict[str, str]:
    """Возвращает словарь gesture → path для всех трёх жестов."""
    return {g: get_animation_path(user_id, g) for g in GESTURE_NAMES}