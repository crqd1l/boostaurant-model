"""Конфиг бота. Токен — только из окружения, никогда не в коде."""

import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# В docker-compose сервисы видят друг друга по имени: http://app:8000
API_URL = os.getenv("API_URL", "http://localhost:8000")

# Сколько блюд показывать официанту. Больше пяти он читать не станет.
TOP_N = 5
