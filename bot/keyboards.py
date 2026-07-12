"""Клавиатуры бота.

Главный принцип для быстрых тегов: их значения ОБЯЗАНЫ совпадать с
menu_items.tags, иначе hard-фильтр в ml/recommend.py их не увидит и заметка
станет декоративной. Поэтому справа от каждой кнопки — реальный тег из БД,
а не выдуманная строка.
"""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

BTN_RECOMMEND = "🍽 Рекомендация"
BTN_PROFILE = "👤 Инфо о посетителе"
BTN_NOTE = "✍️ Оставить заметку"

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_RECOMMEND)],
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_NOTE)],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите действие",
)

# (подпись, тип, тег). Тег — из menu_items.tags, иначе фильтр не сработает.
NOTE_TAGS: list[tuple[str, str, str]] = [
    ("🥛 Аллергия: молочное", "allergens", "dairy"),
    ("🌾 Аллергия: глютен", "allergens", "gluten"),
    ("🥚 Аллергия: яйца", "allergens", "eggs"),
    ("🦐 Аллергия: морепродукты", "allergens", "seafood"),
    ("🌰 Аллергия: соя", "allergens", "soy"),
    ("🌶 Не любит острое", "dislikes", "spicy"),
    ("🥩 Не ест мясо", "dislikes", "meat"),
    ("🍷 Без алкоголя", "dislikes", "alcohol"),
]

TAG_BY_KEY = {f"{kind}:{tag}": (kind, tag, label) for label, kind, tag in NOTE_TAGS}


def note_kb() -> InlineKeyboardMarkup:
    """Быстрые теги + свободный текст. Теги сразу режут выдачу, текст — нет."""
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"tag:{kind}:{tag}")]
        for label, kind, tag in NOTE_TAGS
    ]
    rows.append([InlineKeyboardButton(text="✏️ Написать своими словами",
                                      callback_data="note:free")])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="note:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
