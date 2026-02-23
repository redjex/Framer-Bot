from __future__ import annotations

import asyncio
import os
from io import BytesIO

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
)

from emoji_converter import process_emoji_message
from user_storage import (
    has_custom_animation,
    reset_animation,
    set_animation_path,
    set_custom_emoji_id,
    get_custom_emoji_id,
    GESTURE_NAMES,
)
from video_processor import process_video


# ── FSM ───────────────────────────────────────────────────────────────────────

class ReplaceStates(StatesGroup):
    waiting_gesture_choice = State()
    waiting_emoji_input    = State()


# ── Dispatcher с FSM storage ──────────────────────────────────────────────────

dp = Dispatcher(storage=MemoryStorage())

# Юзернейм бота — заполняется при старте из main.py через set_bot_username()
BOT_USERNAME: str = ""


def set_bot_username(username: str) -> None:
    global BOT_USERNAME
    BOT_USERNAME = username


# ── Утилиты ───────────────────────────────────────────────────────────────────

def _safe_remove(*paths: str | None) -> None:
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception as e:
                print(f"⚠️  Не удалось удалить {p}: {e}")


# Дефолтные кастомные эмодзи (используются если у пользователя нет своего)
_DEFAULT_EMOJI_IDS = {
    "heart":   "5456301958639939262",
    "like":    "5407041870620531251",
    "dislike": "5258475296834730601",
}


def _get_emoji_id_for_user(user_id: int, gesture: str) -> str:
    """Возвращает emoji_id для пользователя: кастомный если есть, иначе дефолтный."""
    custom = get_custom_emoji_id(user_id, gesture)
    return custom if custom else _DEFAULT_EMOJI_IDS[gesture]


def _get_emoji_html(gesture: str, user_id: int) -> str:
    """Генерирует HTML для отображения эмодзи — кастомного или дефолтного."""
    emoji_id = _get_emoji_id_for_user(user_id, gesture)
    return f'<tg-emoji emoji-id="{emoji_id}">📍</tg-emoji>'


def _gesture_btn_label(gesture: str, user_id: int) -> str:
    """Текст для кнопки (без HTML тегов)."""
    custom = "✨ " if has_custom_animation(user_id, gesture) else ""
    names = {"heart": "Сердце", "like": "Лайк", "dislike": "Дизлайк"}
    return f"{custom}{names.get(gesture, gesture.capitalize())}"


def _replace_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=_gesture_btn_label("heart", user_id),
                callback_data="rpl:heart",
                icon_custom_emoji_id=_get_emoji_id_for_user(user_id, "heart"),
            ),
            InlineKeyboardButton(
                text=_gesture_btn_label("like", user_id),
                callback_data="rpl:like",
                icon_custom_emoji_id=_get_emoji_id_for_user(user_id, "like"),
            ),
            InlineKeyboardButton(
                text=_gesture_btn_label("dislike", user_id),
                callback_data="rpl:dislike",
                icon_custom_emoji_id=_get_emoji_id_for_user(user_id, "dislike"),
            ),
        ],
        [InlineKeyboardButton(text="🔄 Сбросить всё", callback_data="rpl:reset_all")],
        [InlineKeyboardButton(
            text="Назад",
            callback_data="rpl:cancel",
            icon_custom_emoji_id="5352759161945867747",
        )],
    ])


def _status_text(user_id: int) -> str:
    lines = []
    for g in GESTURE_NAMES:
        mark = "✨ кастомная" if has_custom_animation(user_id, g) else "стандартная"
        emoji_html = _get_emoji_html(g, user_id)
        lines.append(f"  {emoji_html} {g.capitalize()}: {mark}")
    return "\n".join(lines)


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data="rpl:open")],
        [InlineKeyboardButton(text="🔗 Подключить", callback_data="connect:show")],
    ])


def _main_menu_caption() -> str:
    return (
        "Привет!\n\n"
        "Я бот который поможет отправлять твоим друзьям реакции через жесты\n\n"
        "Нажми кнопку ниже чтобы настроить свои эмодзи на жесты"
    )


# ── /start ────────────────────────────────────────────────────────────────────

@dp.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    await message.answer_photo(
        FSInputFile("img/main_menu.png"),
        caption=_main_menu_caption(),
        reply_markup=_main_menu_keyboard(),
    )


# ── Callback: выбор жеста ────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("rpl:"))
async def cb_replace(callback: CallbackQuery, state: FSMContext) -> None:
    action  = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id

    if action == "open":
        await state.clear()
        await state.set_state(ReplaceStates.waiting_gesture_choice)
        await callback.message.edit_media(
            media=types.InputMediaPhoto(
                media=FSInputFile("img/replace.png"),
                caption=(
                    f"🎨 <b>Заменить эмодзи на свои</b>\n\n"
                    f"Текущие анимации:\n{_status_text(user_id)}\n\n"
                    f"Если у вас нету Telegram Premium, вы не сможете поменять эмодзи на свои. "
                    f"Но вы можете попросить скинуть нужный эмодзи вашего друга.\n"
                    f"<i>✨ — уже стоит кастомная анимация</i>"
                ),
                parse_mode="HTML",
            ),
            reply_markup=_replace_keyboard(user_id),
        )
        await callback.answer()
        return

    if action == "cancel":
        await state.clear()
        try:
            await callback.message.edit_media(
                media=types.InputMediaPhoto(
                    media=FSInputFile("img/main_menu.png"),
                    caption=_main_menu_caption(),
                ),
                reply_markup=_main_menu_keyboard(),
            )
        except Exception:
            pass
        await callback.answer()
        return

    if action == "reset_all":
        count = sum(1 for g in GESTURE_NAMES if reset_animation(user_id, g))
        await state.clear()
        text = f"✅ Сброшено {count} анимаций." if count else "ℹ️ Нечего сбрасывать."
        await callback.answer(text=text, show_alert=False)
        try:
            await callback.message.edit_media(
                media=types.InputMediaPhoto(
                    media=FSInputFile("img/main_menu.png"),
                    caption=_main_menu_caption(),
                ),
                reply_markup=_main_menu_keyboard(),
            )
        except Exception:
            pass
        return

    if action.startswith("reset1:"):
        gesture = action.split(":", 1)[1]
        reset_animation(user_id, gesture)
        # Всплывающий toast, меню обновляем (остаёмся в /replace)
        await callback.answer(text="✅ Сброшено к стандартной", show_alert=False)
        await state.set_state(ReplaceStates.waiting_gesture_choice)
        await callback.message.edit_caption(
            caption=(
                f"🎨 <b>Заменить эмодзи на свои</b>\n\n"
                f"Текущие анимации:\n{_status_text(user_id)}\n\n"
                f"Если у вас нету Telegram Premium, вы не сможете поменять эмодзи на свои. "
                f"Но вы можете попросить скинуть нужный эмодзи вашего друга.\n"
                f"<i>✨ — уже стоит кастомная анимация</i>"
            ),
            parse_mode="HTML",
            reply_markup=_replace_keyboard(user_id),
        )
        return

    if action == "back":
        await state.set_state(ReplaceStates.waiting_gesture_choice)
        await callback.message.edit_caption(
            caption=(
                f"🎨 <b>Заменить эмодзи на свои</b>\n\n"
                f"Текущие анимации:\n{_status_text(user_id)}\n\n"
                f"Если у вас нету Telegram Premium, вы не сможете поменять эмодзи на свои. "
                f"Но вы можете попросить скинуть нужный эмодзи вашего друга.\n"
                f"<i>✨ — уже стоит кастомная анимация</i>"
            ),
            parse_mode="HTML",
            reply_markup=_replace_keyboard(user_id),
        )
        await callback.answer()
        return

    if action in GESTURE_NAMES:
        gesture = action
        await state.update_data(gesture=gesture)
        await state.set_state(ReplaceStates.waiting_emoji_input)

        icon_html = _get_emoji_html(gesture, user_id)

        extra: list[list[InlineKeyboardButton]] = []
        if has_custom_animation(user_id, gesture):
            extra.append([
                InlineKeyboardButton(
                    text="🔄 Сбросить к стандартной",
                    callback_data=f"rpl:reset1:{gesture}",
                )
            ])
        extra.append([InlineKeyboardButton(text="« Назад", callback_data="rpl:back")])

        await callback.message.edit_caption(
            caption=(
                f"✅ Выбран жест: <b>{icon_html} {gesture.capitalize()}</b>\n\n"
                f"Отправь сообщение с кастомным эмодзи — я заменю им анимацию.\n\n"
                f"<i>Кастомные эмодзи вставляются через панель эмодзи "
                f"(нужен Telegram Premium). Просто напиши что угодно "
                f"и вставь нужный анимированный эмодзи в текст.</i>"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=extra),
        )
        await callback.answer()
        return

    await callback.answer("Неизвестное действие")


# ── Ввод эмодзи ───────────────────────────────────────────────────────────────

@dp.message(ReplaceStates.waiting_emoji_input)
async def handle_emoji_input(message: types.Message, state: FSMContext, bot: Bot) -> None:
    data    = await state.get_data()
    gesture = data.get("gesture")
    if not gesture:
        await state.clear()
        return

    has_emoji = bool(
        message.entities and
        any(e.type == "custom_emoji" for e in message.entities)
    )

    if not has_emoji:
        await message.reply(
            "⚠️ В сообщении не найден кастомный эмодзи.\n\n"
            "Вставь анимированный эмодзи прямо в текст сообщения "
            "(нужен Telegram Premium) и отправь снова."
        )
        return

    status_msg = await message.reply("⏳ Конвертирую эмодзи в анимацию...")

    # Извлекаем emoji_id из entities ДО конвертации
    emoji_id: str | None = None
    if message.entities:
        for entity in message.entities:
            if entity.type == "custom_emoji":
                emoji_id = entity.custom_emoji_id
                break

    try:
        webp_data = await process_emoji_message(bot, message)
    except Exception as e:
        print(f"❌ Ошибка при конвертации: {e}")
        webp_data = None

    if not webp_data:
        await status_msg.edit_text(
            "❌ Не удалось конвертировать эмодзи.\n"
            "Попробуй другой эмодзи."
        )
        return

    user_id = message.from_user.id

    # Сохраняем WebP-анимацию и emoji_id
    set_animation_path(user_id, gesture, webp_data)
    if emoji_id:
        set_custom_emoji_id(user_id, gesture, emoji_id)

    await state.clear()

    # Показываем успех
    icon_html = _get_emoji_html(gesture, user_id)
    await status_msg.edit_text(
        f"✅ <b>Готово!</b> Анимация для {icon_html} <b>{gesture.capitalize()}</b> заменена.\n\n"
        f"Она будет использоваться во всех твоих кружках.",
        parse_mode="HTML",
    )

    # Пауза чтобы пользователь успел прочитать, затем удаляем оба сообщения
    await asyncio.sleep(3)
    for msg in (status_msg, message):
        try:
            await msg.delete()
        except Exception:
            pass


# ── Callback: подключение бизнес-бота ────────────────────────────────────────

@dp.callback_query(F.data.startswith("connect:"))
async def cb_connect(callback: CallbackQuery) -> None:
    action = callback.data.split(":", 1)[1]

    if action == "show":
        # Ссылка открывает диалог бота в Telegram — оттуда можно перейти
        # в настройки бизнеса через «...» → «Добавить в бизнес-боты»
        bot_link = f"https://t.me/{BOT_USERNAME}?startattach=1"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Назад", callback_data="connect:back")],
        ])

        await callback.message.edit_media(
            media=types.InputMediaPhoto(
                media=FSInputFile("img/main_menu.png"),
                caption=(
                    "🔗 <b>Как подключить бота</b>\n\n"
                    "Чтобы бот мог читать кружки и удалять исходящие сообщения, "
                    "нужно добавить его как бизнес-бот в настройках Telegram:\n\n"
                    "<b>1.</b> Открой <b>Настройки</b> → <b>Telegram для бизнеса</b>\n"
                    "<b>2.</b> Нажми <b>Чат-боты</b>\n"
                    "<b>3.</b> В строке поиска введи <code>@"
                    + BOT_USERNAME +
                    "</code>\n"
                    "<b>4.</b> Выбери бота и выдай разрешения:\n"
                    "   • ✅ Читать сообщения\n"
                    "   • ✅ Удалять исходящие сообщения\n\n"
                    "<i>После подключения бот начнёт автоматически обрабатывать твои кружки.</i>"
                ),
                parse_mode="HTML",
            ),
            reply_markup=keyboard,
        )
        await callback.answer()
        return

    if action == "back":
        await callback.message.edit_media(
            media=types.InputMediaPhoto(
                media=FSInputFile("img/main_menu.png"),
                caption=_main_menu_caption(),
            ),
            reply_markup=_main_menu_keyboard(),
        )
        await callback.answer()
        return

    await callback.answer()


# ── Кружки напрямую боту ─────────────────────────────────────────────────────

@dp.message(F.video_note)
async def handle_direct_video_note(message: types.Message, bot: Bot) -> None:
    """Обрабатывает кружки, которые пользователь отправляет боту напрямую."""
    user_id = message.from_user.id
    fid     = message.video_note.file_id

    in_p  = f"in_direct_{fid}.mp4"
    out_p = f"out_direct_{fid}.mp4"

    status_msg = await message.reply("⏳ Обрабатываю кружок...")

    try:
        file_info = await bot.get_file(fid)
        await bot.download_file(file_info.file_path, in_p)

        await asyncio.to_thread(process_video, in_p, out_p, user_id)

        _safe_remove(in_p)
        in_p = None

        await bot.send_video_note(
            chat_id=message.chat.id,
            video_note=FSInputFile(out_p),
        )
        try:
            await status_msg.delete()
        except Exception:
            pass

        print(f"✅ Прямой кружок обработан для user {user_id}")

    except Exception as e:
        print(f"❌ Ошибка обработки прямого кружка: {e}")
        try:
            await status_msg.edit_text("❌ Не удалось обработать кружок.")
        except Exception:
            pass
    finally:
        await asyncio.sleep(3)
        _safe_remove(in_p, out_p)


@dp.business_message(F.video_note)
async def handle_business_video_note(message: types.Message, bot: Bot) -> None:
    bus_conn_id = message.business_connection_id
    connection  = await bot.get_business_connection(bus_conn_id)

    if message.from_user.id != connection.user.id:
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    msg_id  = message.message_id
    fid     = message.video_note.file_id

    in_p  = f"in_{fid}.mp4"
    out_p = f"out_{fid}.mp4"

    try:
        file_info = await bot.get_file(fid)
        await bot.download_file(file_info.file_path, in_p)

        try:
            await bot.delete_business_messages(
                business_connection_id=bus_conn_id,
                message_ids=[msg_id],
            )
            print(f"✅ Удалено сообщение {msg_id}")
        except Exception as e:
            print(f"❌ Ошибка удаления сообщения: {e}")

        await asyncio.to_thread(process_video, in_p, out_p, user_id)

        _safe_remove(in_p)
        in_p = None

        await bot.send_video_note(
            chat_id=chat_id,
            video_note=FSInputFile(out_p),
            business_connection_id=bus_conn_id,
        )
        print(f"✅ Видео отправлено в чат {chat_id}")

    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
    finally:
        await asyncio.sleep(3)
        _safe_remove(in_p, out_p)