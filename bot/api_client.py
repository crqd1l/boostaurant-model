"""HTTP-клиент к сервису рекомендаций (api/main.py).

Бот НЕ импортирует ml.recommend напрямую: архитектура из гранта — «бот отправляет
запрос в FastAPI». Так бота можно деплоить отдельно от моделей, а тяжёлые numpy/
surprise не тянутся в его образ.

ApiError — единственное исключение, которое видят хэндлеры. Всё остальное
(таймауты, 500, отвалившаяся сеть) заворачивается в него с человеческим текстом:
официант стоит у столика, ему нужно понятное сообщение, а не traceback.
"""

import logging

import aiohttp

from bot.config import API_URL, TOP_N

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=10)


class ApiError(Exception):
    """Ошибка, которую можно показать официанту как есть."""


async def _request(method: str, path: str, **kwargs) -> dict:
    url = f"{API_URL}{path}"
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.request(method, url, **kwargs) as resp:
                if resp.status == 404:
                    # 404 — не сбой, а нормальный ответ: за столиком никого нет.
                    detail = (await resp.json()).get("detail", "Не найдено")
                    raise ApiError(detail)
                if resp.status >= 400:
                    body = await resp.text()
                    logger.error("API %s %s → %s: %s", method, url, resp.status, body[:300])
                    raise ApiError("Сервис рекомендаций недоступен. Попробуйте ещё раз.")
                return await resp.json()

    except aiohttp.ClientError as exc:
        logger.error("Сеть недоступна: %s", exc)
        raise ApiError("Нет связи с сервисом. Попробуйте ещё раз.") from exc
    except TimeoutError as exc:
        logger.error("Таймаут: %s %s", method, url)
        raise ApiError("Сервис долго отвечает. Попробуйте ещё раз.") from exc


async def get_recommendations(table: int, n: int = TOP_N) -> dict:
    return await _request("GET", "/recommendations", params={"table": table, "n": n})


async def get_profile(table: int) -> dict:
    return await _request("GET", "/profile", params={"table": table})


async def save_note(
    table: int,
    allergens: list[str] | None = None,
    dislikes: list[str] | None = None,
    note: str | None = None,
) -> dict:
    return await _request(
        "POST", "/preferences",
        params={"table": table},
        json={"allergens": allergens or [], "dislikes": dislikes or [], "note": note},
    )
