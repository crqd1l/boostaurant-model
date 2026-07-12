"""JSON от API → текст для официанта.

Вынесено отдельно от хэндлеров, чтобы тестировать без Telegram и без токена.

Про source. API возвращает, какой слой сработал, и официанту это важно: одно
дело «мы знаем этого гостя», другое — «просто популярное в зале». Показывать
голое слово "svd" бессмысленно, поэтому переводим в human-readable подпись.
"""

SOURCE_LABEL = {
    "svd": "персонально для гостя",
    "coldstart": "по похожим гостям",       # мало истории — кластер k-means
    "popularity": "популярное в зале",      # о госте не знаем ничего
}

GENDER = {0: "", 1: "М", 2: "Ж"}

# Обратный перевод тегов в человеческие слова: в БД они лежат по-английски,
# потому что совпадают с menu_items.tags.
TAG_RU = {
    "dairy": "молочное", "gluten": "глютен", "eggs": "яйца", "nuts": "орехи",
    "seafood": "морепродукты", "fish": "рыба", "soy": "соя", "meat": "мясо",
    "alcohol": "алкоголь", "spicy": "острое", "pork": "свинина",
    "beef": "говядина", "chicken": "курица",
}


def _ru(tags: list[str]) -> str:
    return ", ".join(TAG_RU.get(t, t) for t in tags)


def format_recommendations(data: dict) -> str:
    table = data.get("table_number")
    head = f"🍽 <b>Рекомендации — столик {table}</b>" if table else "🍽 <b>Рекомендации</b>"
    label = SOURCE_LABEL.get(data["source"], data["source"])

    lines = [head, f"<i>{label}</i>", ""]
    for i, item in enumerate(data["items"], 1):
        lines.append(f"{i}. <b>{item['name']}</b> — {item['price']:.0f} ₽")
        lines.append(f"    <i>{item['category']}</i>")

    if data["source"] == "popularity":
        lines += ["", "⚠️ Гость нам незнаком — это просто топ продаж."]

    return "\n".join(lines)


def format_profile(p: dict) -> str:
    table = p.get("table_number")
    lines = [f"👤 <b>Гость — столик {table}</b>" if table else "👤 <b>Гость</b>", ""]

    # Имени в БД нет намеренно (для ML бесполезно, в проде придёт из iiko),
    # поэтому идентифицируем гостя телефоном.
    if p.get("phone"):
        lines.append(f"📞 {p['phone']}")

    demo = [x for x in (GENDER.get(p.get("gender") or 0),
                        f"{p['age']} лет" if p.get("age") else None) if x]
    if demo:
        lines.append("🎂 " + " · ".join(demo))

    if p.get("loyalty_tier"):
        lines.append(f"🏅 Лояльность: <b>{p['loyalty_tier']}</b>")

    lines.append(f"📊 Визитов: <b>{p['visits']}</b> · средний чек: <b>{p['avg_check']:.0f} ₽</b>")
    if p.get("last_visit"):
        lines.append(f"🕐 Последний визит: {p['last_visit']}")

    if p.get("favorites"):
        lines += ["", "<b>Любимые блюда:</b>"]
        for f in p["favorites"]:
            lines.append(f"  • {f['name']} <i>({f['qty']}×)</i>")

    # Аллергены — самое важное на экране. Их официант обязан увидеть.
    if p.get("allergens"):
        lines += ["", f"⛔️ <b>АЛЛЕРГИЯ: {_ru(p['allergens'])}</b>"]
    if p.get("dislikes"):
        lines.append(f"👎 Не любит: {_ru(p['dislikes'])}")
    if p.get("note"):
        lines += ["", f"📝 <i>{p['note']}</i>"]

    if not p["visits"]:
        lines += ["", "ℹ️ Гость у нас впервые — истории заказов нет."]

    return "\n".join(lines)


def format_saved_note(saved: dict) -> str:
    lines = ["✅ <b>Записано</b>", ""]
    if saved.get("allergens"):
        lines.append(f"⛔️ Аллергия: {_ru(saved['allergens'])}")
    if saved.get("dislikes"):
        lines.append(f"👎 Не любит: {_ru(saved['dislikes'])}")
    if saved.get("note"):
        lines.append(f"📝 {saved['note']}")

    if saved.get("allergens") or saved.get("dislikes"):
        lines += ["", "<i>Эти блюда больше не попадут в рекомендации.</i>"]
    return "\n".join(lines)
