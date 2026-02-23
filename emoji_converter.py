from __future__ import annotations

import gzip
import io
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from config import FFMPEG_PATH

if TYPE_CHECKING:
    from aiogram import Bot

EMOJI_OUTPUT_SIZE = 512

# URL cairo DLL из официального GTK Windows build (только DLL, ~1.5MB)
_CAIRO_DLL_URL = (
    "https://github.com/nicowillis/cairo-dll-windows/releases/download/v1.18.0/"
    "libcairo-2.dll"
)
# Путь куда кладём DLL — рядом с main.py
_CAIRO_DLL_PATH = Path(__file__).parent / "libcairo-2.dll"


def _ensure_cairo_dll() -> bool:
    """
    Проверяет наличие libcairo-2.dll и скачивает если нужно.
    Возвращает True если DLL доступна.
    """
    # Если уже в PATH или рядом — OK
    if shutil.which("libcairo-2.dll") or _CAIRO_DLL_PATH.exists():
        if _CAIRO_DLL_PATH.exists():
            # Добавляем папку проекта в PATH чтобы pycairo нашёл DLL
            project_dir = str(_CAIRO_DLL_PATH.parent)
            if project_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = project_dir + os.pathsep + os.environ.get("PATH", "")
        return True

    print("ℹ️  libcairo-2.dll не найден, скачиваю...")
    try:
        # Скачиваем напрямую из GitHub releases
        urls = [
            # Вариант 1: прямой DLL
            "https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases/download/2022-01-04/gtk3-runtime-3.24.31-2022-01-04-ts-win64.exe",
        ]

        # Используем более надёжный источник — mingw64 пакет
        dll_url = (
            "https://packages.msys2.org/package/mingw-w64-x86_64-cairo"
        )

        # Самый надёжный: скачиваем из prebuilt GTK zip
        # https://github.com/nicowillis/cairo-dll-windows — маленький репо только с DLL
        simple_urls = [
            "https://raw.githubusercontent.com/preshing/cairo-windows/master/lib/x64/cairo.dll",
        ]

        for url in simple_urls:
            try:
                print(f"ℹ️  Пробую: {url}")
                urllib.request.urlretrieve(url, _CAIRO_DLL_PATH.with_name("libcairo-2.dll"))
                # Проверяем что скачали DLL а не HTML
                data = _CAIRO_DLL_PATH.read_bytes()
                if data[:2] == b"MZ":  # Windows PE header
                    print(f"✅ cairo DLL скачан: {_CAIRO_DLL_PATH}")
                    project_dir = str(_CAIRO_DLL_PATH.parent)
                    os.environ["PATH"] = project_dir + os.pathsep + os.environ.get("PATH", "")
                    return True
                else:
                    _CAIRO_DLL_PATH.unlink(missing_ok=True)
            except Exception as e:
                print(f"⚠️  {e}")
                continue

        return False

    except Exception as e:
        print(f"❌ Не удалось скачать cairo DLL: {e}")
        return False


def _patch_cairo_path() -> bool:
    """
    Добавляет папку проекта в PATH и пробует импортировать cairo.
    Возвращает True если cairo доступен.
    """
    # Сначала добавляем текущую папку в PATH (на случай если DLL там)
    project_dir = str(Path(__file__).parent)
    current_path = os.environ.get("PATH", "")
    if project_dir not in current_path:
        os.environ["PATH"] = project_dir + os.pathsep + current_path

    # Пробуем импортировать
    try:
        import cairo  # noqa
        return True
    except (ImportError, OSError):
        pass

    # DLL нет — пробуем скачать
    if _ensure_cairo_dll():
        try:
            # Перезагружаем модуль cairo
            if "cairo" in sys.modules:
                del sys.modules["cairo"]
            if "_cairo" in sys.modules:
                del sys.modules["_cairo"]
            import cairo  # noqa
            return True
        except (ImportError, OSError) as e:
            print(f"⚠️  cairo всё ещё недоступен после скачивания DLL: {e}")

    return False


# ── Скачивание ────────────────────────────────────────────────────────────────

async def download_custom_emoji(
    bot: "Bot", custom_emoji_id: str
) -> tuple[bytes, str] | tuple[None, None]:
    try:
        stickers = await bot.get_custom_emoji_stickers([custom_emoji_id])
        if not stickers:
            print("❌ get_custom_emoji_stickers вернул пустой список")
            return None, None

        sticker   = stickers[0]
        file_info = await bot.get_file(sticker.file_id)
        file_path = file_info.file_path or ""
        ext = Path(file_path).suffix.lower()
        if ext not in (".tgs", ".webm", ".webp"):
            ext = ".tgs"

        print(f"ℹ️  Эмодзи: file_path={file_path!r}  ext={ext}")
        buf = io.BytesIO()
        await bot.download_file(file_info.file_path, buf)
        data = buf.getvalue()
        print(f"ℹ️  Скачано {len(data)} байт")
        return data, ext

    except Exception as e:
        print(f"❌ Ошибка скачивания эмодзи: {e}")
        return None, None


async def get_custom_emoji_id_from_message(message) -> str | None:
    if not message.entities:
        return None
    for entity in message.entities:
        if entity.type == "custom_emoji":
            return entity.custom_emoji_id
    return None


# ── Утилиты ───────────────────────────────────────────────────────────────────

def _frames_to_webp(frames: list, duration_ms: int) -> bytes:
    from PIL import Image
    resized = []
    for f in frames:
        img = f.convert("RGBA")
        if img.size != (EMOJI_OUTPUT_SIZE, EMOJI_OUTPUT_SIZE):
            img = img.resize((EMOJI_OUTPUT_SIZE, EMOJI_OUTPUT_SIZE), Image.LANCZOS)
        resized.append(img)
    buf = io.BytesIO()
    if len(resized) == 1:
        resized[0].save(buf, format="WEBP", quality=90)
    else:
        resized[0].save(buf, format="WEBP", save_all=True,
                        append_images=resized[1:],
                        duration=duration_ms, loop=0, quality=90)
    return buf.getvalue()


def _process_webp(data: bytes) -> bytes:
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        if w == EMOJI_OUTPUT_SIZE and h == EMOJI_OUTPUT_SIZE:
            return data
        frames, durations = [], []
        try:
            for i in range(getattr(img, "n_frames", 1)):
                img.seek(i)
                frames.append(img.convert("RGBA").resize(
                    (EMOJI_OUTPUT_SIZE, EMOJI_OUTPUT_SIZE), Image.LANCZOS))
                durations.append(img.info.get("duration", 50))
        except EOFError:
            pass
        buf = io.BytesIO()
        if len(frames) == 1:
            frames[0].save(buf, format="WEBP", lossless=True)
        else:
            frames[0].save(buf, format="WEBP", save_all=True,
                           append_images=frames[1:], duration=durations, loop=0)
        return buf.getvalue()
    except Exception as e:
        print(f"❌ Ошибка обработки WebP: {e}")
        return data


def _webm_to_webp(data: bytes) -> bytes | None:
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            in_path    = os.path.join(tmpdir, "input.webm")
            frames_dir = os.path.join(tmpdir, "frames")
            os.makedirs(frames_dir)
            Path(in_path).write_bytes(data)

            r = subprocess.run([
                FFMPEG_PATH, "-y", "-i", in_path,
                "-vf", f"scale={EMOJI_OUTPUT_SIZE}:{EMOJI_OUTPUT_SIZE}:flags=lanczos",
                "-pix_fmt", "rgba",
                os.path.join(frames_dir, "frame_%04d.png"),
            ], capture_output=True, timeout=60)

            if r.returncode != 0:
                print(f"❌ FFmpeg: {r.stderr.decode(errors='replace')[-300:]}")
                return None

            fps_raw = subprocess.run(
                [FFMPEG_PATH, "-i", in_path], capture_output=True, timeout=10
            ).stderr.decode(errors="replace")
            fps = 30.0
            for token in fps_raw.split():
                if "fps" in token:
                    try: fps = float(token.replace("fps", "").strip(",")); break
                    except ValueError: pass

            from PIL import Image
            frame_files = sorted(Path(frames_dir).glob("frame_*.png"))
            if not frame_files:
                return None
            frames = [Image.open(f) for f in frame_files]
            result = _frames_to_webp(frames, int(1000 / max(fps, 1)))
            print(f"✅ WebM→WebP: {len(result)} байт, {len(frames)} кадров")
            return result
    except Exception as e:
        print(f"❌ WebM→WebP ошибка: {e}")
        return None


# ── TGS конвертация ───────────────────────────────────────────────────────────

def _tgs_to_webp(tgs_data: bytes) -> bytes | None:
    """
    TGS → animated WebP через rlottie-python.
    Использует from_tgs() и render_pillow_frame() — чистый Python, без cairo.
    """
    try:
        import rlottie_python as rl
    except ImportError as e:
        print(f"❌ rlottie-python не установлен: {e}")
        return None

    from PIL import Image

    # from_tgs ожидает путь к .tgs файлу (сырые gzip-байты, не распакованные)
    tgs_path = None
    try:
        tgs_path = str(Path(__file__).parent / "_tmp_emoji.tgs")
        Path(tgs_path).write_bytes(tgs_data)
        anim = rl.LottieAnimation.from_tgs(tgs_path)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ rlottie from_tgs: {e}")
        return None
    finally:
        if tgs_path and os.path.exists(tgs_path):
            os.unlink(tgs_path)

    total_frames = anim.lottie_animation_get_totalframe()
    fps          = anim.lottie_animation_get_framerate()
    duration_ms  = int(1000 / max(fps, 1))
    step         = 2 if fps >= 50 else 1

    print(f"ℹ️  TGS (rlottie): {total_frames} кадров, {fps} fps")

    import numpy as np

    W = H = EMOJI_OUTPUT_SIZE
    buf_size = W * H * 4  # BGRA, 4 байта на пиксель

    frames: list[Image.Image] = []
    for frame_num in range(0, total_frames, step):
        try:
            raw = anim.lottie_animation_render(
                frame_num=frame_num,
                buffer_size=buf_size,
                width=W,
                height=H,
                bytes_per_line=W * 4,
            )
            arr  = np.frombuffer(raw, dtype=np.uint8).reshape(H, W, 4)
            rgba = arr[:, :, [2, 1, 0, 3]]  # BGRA → RGBA
            frames.append(Image.fromarray(rgba, "RGBA"))
        except Exception as e:
            import traceback
            print(f"❌ render кадр {frame_num}: {e}")
            traceback.print_exc()
            break

    if not frames:
        print("❌ Ни одного кадра не отрендерено")
        return None

    result = _frames_to_webp(frames, duration_ms * step)
    print(f"✅ TGS→WebP (rlottie): {len(result)} байт, {len(frames)} кадров")
    return result


# ── Публичный API ─────────────────────────────────────────────────────────────

def convert_to_webp(raw_data: bytes, source_ext: str) -> bytes | None:
    ext = source_ext.lower()
    print(f"ℹ️  Конвертация: формат={ext}, размер={len(raw_data)} байт")
    if ext == ".webp":  return _process_webp(raw_data)
    if ext == ".webm":  return _webm_to_webp(raw_data)
    if ext == ".tgs":   return _tgs_to_webp(raw_data)
    print(f"⚠️  Неизвестный формат: {ext}")
    return None


async def process_emoji_message(bot: "Bot", message) -> bytes | None:
    emoji_id = await get_custom_emoji_id_from_message(message)
    if not emoji_id:
        print("❌ custom_emoji_id не найден в entities")
        return None
    print(f"ℹ️  Обрабатываю эмодзи id={emoji_id}")
    raw, ext = await download_custom_emoji(bot, emoji_id)
    if raw is None:
        return None
    return convert_to_webp(raw, ext)