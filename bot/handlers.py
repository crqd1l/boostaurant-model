"""Хэндлеры бота официанта.

Все три кнопки сначала спрашивают номер столика, поэтому бот должен помнить,
ЗАЧЕМ он его спросил — это и есть FSM. В моке состояния не было, и номер
«ловился» сравнением текста с "4"/"5", из-за чего работали ровно два столика.

Сценарий:
    [Рекомендация] → «Введите номер столика» → 5 → топ-5 блюд
    [Инфо]         → «Введите номер столика» → 5 → профиль гостя
    [Заметка]      → «Введите номер столика» → 5 → теги/текст → сохранено
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.api_client import ApiError, get_profile, get_recommendations, save_note
from bot.formatters import format_profile, format_recommendations, format_saved_note
from bot.keyboards import (
    BTN_NOTE,
    BTN_PROFILE,
    BTN_RECOMMEND,
    TAG_BY_KEY,
    main_kb,
    note_kb,
)

logger = logging.getLogger(__name__)
router = Router()


class Waiter(StatesGroup):
    # Ждём номер столика. Зачем именно — хранится в data["action"].
    waiting_table = State()
    # Ждём свободный текст заметки (столик уже известен).
    waiting_note = State()


ASK_TABLE = "Введите номер столика:"


# --- вход -------------------------------------------------------------------
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 <b>Boostaurant</b> — помощник официанта.\n\n"
        "Выберите действие:",
        reply_markup=main_kb,
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=main_kb)


# --- три кнопки: все спрашивают столик, но с разным намерением ---------------
@router.message(F.text == BTN_RECOMMEND)
async def ask_table_for_recommend(message: Message, state: FSMContext) -> None:
    await state.set_state(Waiter.waiting_table)
    await state.update_data(action="recommend")
    await message.answer(ASK_TABLE)


@router.message(F.text == BTN_PROFILE)
async def ask_table_for_profile(message: Message, state: FSMContext) -> None:
    await state.set_state(Waiter.waiting_table)
    await state.update_data(action="profile")
    await message.answer(ASK_TABLE)


@router.message(F.text == BTN_NOTE)
async def ask_table_for_note(message: Message, state: FSMContext) -> None:
    await state.set_state(Waiter.waiting_table)
    await state.update_data(action="note")
    await message.answer(ASK_TABLE)


# --- пришёл номер столика ---------------------------------------------------
@router.message(Waiter.waiting_table)
async def got_table(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("Нужен номер столика — целое число. Например: 5")
        return

    table = int(raw)
    action = (await state.get_data()).get("action")

    try:
        if action == "recommend":
            data = await get_recommendations(table)
            await state.clear()
            await message.answer(format_recommendations(data), reply_markup=main_kb)

        elif action == "profile":
            data = await get_profile(table)
            await state.clear()
            await message.answer(format_profile(data), reply_markup=main_kb)

        elif action == "note":
            # Столик держим в state: он понадобится после выбора тега/текста.
            await state.update_data(table=table)
            await message.answer(
                f"Что отметить у гостя за столиком {table}?",
                reply_markup=note_kb(),
            )
        else:
            await state.clear()
            await message.answer("Не понял действие. Начните заново.", reply_markup=main_kb)

    except ApiError as exc:
        # Столик пуст или сервис недоступен — это ожидаемо, показываем как есть.
        await state.clear()
        await message.answer(f"⚠️ {exc}", reply_markup=main_kb)


# --- заметка: быстрый тег ---------------------------------------------------
@router.callback_query(F.data.startswith("tag:"))
async def note_tag(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.removeprefix("tag:")
    entry = TAG_BY_KEY.get(key)
    table = (await state.get_data()).get("table")

    if not entry or table is None:
        await state.clear()
        await callback.message.edit_text("Что-то пошло не так, начните заново.")
        await callback.answer()
        return

    kind, tag, label = entry
    try:
        saved = await save_note(
            table,
            allergens=[tag] if kind == "allergens" else None,
            dislikes=[tag] if kind == "dislikes" else None,
        )
        await callback.message.edit_text(format_saved_note(saved))
        await callback.message.answer("Готово.", reply_markup=main_kb)
    except ApiError as exc:
        await callback.message.edit_text(f"⚠️ {exc}")
    finally:
        await state.clear()
        await callback.answer()


# --- заметка: свободный текст -----------------------------------------------
@router.callback_query(F.data == "note:free")
async def note_free_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Waiter.waiting_note)
    await callback.message.edit_text(
        "Напишите заметку о госте.\n"
        "<i>Например: «просит соус отдельно», «любит столик у окна»</i>"
    )
    await callback.answer()


@router.callback_query(F.data == "note:cancel")
async def note_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.message.answer("Выберите действие:", reply_markup=main_kb)
    await callback.answer()


@router.message(Waiter.waiting_note)
async def note_free_save(message: Message, state: FSMContext) -> None:
    table = (await state.get_data()).get("table")
    text = (message.text or "").strip()

    if table is None:
        await state.clear()
        await message.answer("Не помню столик. Начните заново.", reply_markup=main_kb)
        return
    if not text:
        await message.answer("Пустая заметка. Напишите текст или /cancel.")
        return

    try:
        saved = await save_note(table, note=text)
        await message.answer(format_saved_note(saved), reply_markup=main_kb)
    except ApiError as exc:
        await message.answer(f"⚠️ {exc}", reply_markup=main_kb)
    finally:
        await state.clear()


# --- всё остальное ----------------------------------------------------------
@router.message()
async def fallback(message: Message) -> None:
    await message.answer("Выберите действие на клавиатуре 👇", reply_markup=main_kb)
