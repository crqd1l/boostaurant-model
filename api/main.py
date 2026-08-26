"""FastAPI — сервис рекомендаций. То, во что ходит Telegram-бот.

    GET  /recommendations?customer_id=<uuid>  → топ-5 для клиента
    GET  /recommendations?table=<n>           → топ-5 для гостя за столиком
    POST /retrain                             → переобучить модели (демо)
    GET  /health                              → жив ли сервис и загружены ли модели

Столик резолвится через api/iiko_stub.py — он отдаёт customer_id ровно так же,
как это будет делать боевая интеграция, поэтому здесь ветка одна.
"""

import logging
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from api.iiko_stub import customer_id_by_table
from api.profile import get_profile, save_preferences
from ml.recommend import load_kmeans, load_model, recommend

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(
    title="Boostaurant Recommendations",
    description="ИИ-ассистент официанта: топ-5 персональных рекомендаций по меню",
    version="0.1.0",
)


class Item(BaseModel):
    id: str
    name: str
    category: str
    price: float
    score: float


class Recommendations(BaseModel):
    customer_id: str
    # Какой слой сработал. Не отладка: официанту важно отличать персональную
    # рекомендацию от "просто популярного", и бот может показать это иначе.
    source: Literal["svd", "coldstart", "popularity"]
    items: list[Item]
    table_number: int | None = None


class Favorite(BaseModel):
    name: str
    category: str
    qty: int


class Profile(BaseModel):
    customer_id: str
    phone: str | None = None
    age: int | None = None
    birthday: date | None = None
    loyalty_tier: str | None = None
    first_order_date: date | None = None
    last_visit: date | None = None
    visits: int
    avg_check: float
    favorites: list[Favorite]
    allergens: list[str]
    dislikes: list[str]
    note: str | None = None
    table_number: int | None = None


class PreferencesIn(BaseModel):
    # Теги должны пересекаться с menu_items.tags, иначе hard-фильтр их не увидит.
    allergens: list[str] = []
    dislikes: list[str] = []
    note: str | None = None


class Preferences(BaseModel):
    customer_id: str
    allergens: list[str]
    dislikes: list[str]
    note: str | None = None


@app.get("/health")
def health() -> dict:
    """Жив ли сервис и загружаются ли модели с диска."""
    models = {}
    for name, loader in (("svd", load_model), ("kmeans", load_kmeans)):
        try:
            loader()
            models[name] = "ok"
        except FileNotFoundError:
            models[name] = "missing"
    # Без моделей сервис не бесполезен: popularity-fallback работает на голом SQL.
    return {"status": "ok", "models": models}


def _resolve_customer(customer_id: str | None, table: int | None) -> str:
    """customer_id ИЛИ table → customer_id. Бот всегда присылает столик."""
    if (customer_id is None) == (table is None):
        raise HTTPException(400, "Укажи ровно один параметр: customer_id ИЛИ table")

    if table is None:
        return customer_id

    resolved = customer_id_by_table(table)
    if resolved is None:
        # В проде: за столиком нет ни активного заказа, ни брони.
        raise HTTPException(404, f"За столиком {table} никого нет")
    return resolved


@app.get("/recommendations", response_model=Recommendations)
def get_recommendations(
    customer_id: str | None = Query(None, description="UUID клиента"),
    table: int | None = Query(None, ge=1, description="Номер столика (заглушка iiko)"),
    n: int = Query(5, ge=1, le=20, description="Сколько блюд вернуть"),
) -> Recommendations:
    """Топ-n рекомендаций. Клиент задаётся либо напрямую, либо номером столика."""
    cid = _resolve_customer(customer_id, table)
    return Recommendations(**recommend(cid, n=n), table_number=table)


@app.get("/profile", response_model=Profile)
def get_customer_profile(
    customer_id: str | None = Query(None, description="UUID клиента"),
    table: int | None = Query(None, ge=1, description="Номер столика"),
) -> Profile:
    """Инфо о госте для официанта: лояльность, визиты, любимые блюда, аллергены."""
    cid = _resolve_customer(customer_id, table)
    profile = get_profile(cid)
    if profile is None:
        raise HTTPException(404, "Клиент не найден")
    return Profile(**profile, table_number=table)


@app.post("/preferences", response_model=Preferences)
def post_preferences(
    body: PreferencesIn,
    customer_id: str | None = Query(None, description="UUID клиента"),
    table: int | None = Query(None, ge=1, description="Номер столика"),
) -> Preferences:
    """Заметка официанта о госте.

    allergens/dislikes — не декоративные: они сразу идут в hard-фильтр, и
    следующая рекомендация этому гостю будет уже без запрещённых блюд.
    Существующие значения дополняются, а не перезатираются: официант дописывает
    к тому, что отметил коллега в прошлый визит.
    """
    cid = _resolve_customer(customer_id, table)
    if not (body.allergens or body.dislikes or body.note):
        raise HTTPException(400, "Пустая заметка: укажи allergens, dislikes или note")

    saved = save_preferences(cid, body.allergens, body.dislikes, body.note)
    return Preferences(**saved)


@app.post("/retrain")
def retrain() -> dict:
    """Переобучить SVD и k-means на текущих данных БД.

    Синхронно — это ДЕМО-эндпоинт. В проде переобучение делается фоновой задачей
    по расписанию (в гранте — еженедельно) и в паре с синхронизатором iiko:
    сначала свежие заказы приезжают в нашу БД (Поток B), только потом обучение.
    На 300 клиентах это секунды, но на 5000 — минуты, и HTTP отвалится по таймауту.

    Зачем это вообще нужно: клиент попадает в матрицу SVD ТОЛЬКО при
    переобучении. До него новый гость, сколько бы раз ни пришёл, обслуживается
    слоем coldstart. Retrain — момент, когда он переезжает на персонализацию.
    """
    trained = []
    for module in ("ml.train_svd", "ml.train_kmeans"):
        proc = subprocess.run(
            [sys.executable, "-m", module],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,  # uvicorn могли запустить откуда угодно
        )
        if proc.returncode != 0:
            logger.error("%s упал (rc=%s):\n%s", module, proc.returncode, proc.stderr)
            raise HTTPException(500, f"{module} упал: {proc.stderr[-500:]}")
        trained.append(module)

    # КРИТИЧНО. Модели закэшированы через @lru_cache — без сброса процесс продолжит
    # отдавать СТАРУЮ модель из памяти, и эндпоинт молча соврёт об успехе:
    # .pkl на диске новый, а рекомендации те же самые.
    load_model.cache_clear()
    load_kmeans.cache_clear()

    return {"status": "ok", "retrained": trained}
