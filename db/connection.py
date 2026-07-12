"""Подключение к PostgreSQL.

DATABASE_URL берётся из окружения (docker-compose передаёт его в контейнер app).
Локальный запуск вне docker: экспортируй DATABASE_URL или положись на дефолт,
который смотрит на проброшенный наружу порт 5432.
"""

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

DEFAULT_URL = "postgresql://boostaurant:boostaurant@localhost:5432/boostaurant"

DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_URL)

# pool_pre_ping — соединение может протухнуть между редкими запросами API.
engine: Engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)


def get_engine() -> Engine:
    return engine
