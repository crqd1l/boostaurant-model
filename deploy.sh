#!/bin/bash
#
# Деплой Boostaurant на VPS (Beget).
#
#   ./deploy.sh              — выкатить код, пересобрать, перезапустить
#   ./deploy.sh --seed       — то же + сгенерировать данные и обучить модели
#   ./deploy.sh --tunnel     — ssh-туннель: API сервера на твоём localhost:8000
#   ./deploy.sh --logs       — показать логи и выйти
#   ./deploy.sh --down       — погасить всё на сервере
#
# Первый запуск делай с --seed: без данных модели не обучить, а без моделей
# API отдаёт только popularity-fallback.
#
# Публичный IP не нужен. Бот работает через long polling — сам стучится к
# api.telegram.org, входящих соединений не принимает. API вызывает только бот,
# изнутри docker-сети. Наружу не открыт НИЧЕГО, а чтобы дёргать /docs и
# /retrain с локали — есть --tunnel.
#
# Требования к серверу: Ubuntu 22.04+, root, Docker (поставится сам).
# 4 ГБ RAM / 2 vCPU / 20 ГБ — упирается в сборку scikit-surprise, само
# приложение ест ~180 МБ. Публичный IP покупать НЕ нужно (см. выше).

set -euo pipefail

# --- НАСТРОЙ ЭТО -------------------------------------------------------------
SERVER_USER=root
SERVER_IP=CHANGE_ME            # IP от Beget
# -----------------------------------------------------------------------------

PROJECT_NAME=boostaurant
REMOTE_DIR=/home/$PROJECT_NAME
ARCHIVE=$PROJECT_NAME.tar.gz
COMPOSE="docker compose -f docker-compose.prod.yml --profile bot"

if [[ "$SERVER_IP" == "CHANGE_ME" ]]; then
  echo "❌ Впиши SERVER_IP в deploy.sh (строка 19)"
  exit 1
fi

SSH="ssh $SERVER_USER@$SERVER_IP"

# --- быстрые команды, не требующие выкатки -----------------------------------
case "${1:-}" in
  --logs)
    $SSH "cd $REMOTE_DIR && $COMPOSE logs --tail=80 -f"
    exit 0
    ;;
  --down)
    echo "🛑 Гашу сервисы на $SERVER_IP…"
    $SSH "cd $REMOTE_DIR && $COMPOSE down"
    echo "✅ Остановлено. Данные в volume pgdata сохранены."
    exit 0
    ;;
  --tunnel)
    echo "🔌 Туннель до $SERVER_IP…"
    echo ""
    echo "   Пока он открыт, API сервера доступен на твоей машине:"
    echo "     http://localhost:8000/docs"
    echo "     curl 'http://localhost:8000/recommendations?table=5'"
    echo "     curl -X POST http://localhost:8000/retrain"
    echo ""
    echo "   Ctrl+C — закрыть."
    echo ""
    # -N: не выполнять команду, только форвардить порт.
    # Если 8000 занят локальным compose — ssh честно скажет, что порт занят.
    exec ssh -N -L 8000:localhost:8000 "$SERVER_USER@$SERVER_IP"
    ;;
esac

SEED=false
[[ "${1:-}" == "--seed" ]] && SEED=true

echo "🚀 ДЕПЛОЙ BOOSTAURANT → $SERVER_IP"
echo "========================================"

# .env с токеном бота обязателен: без него бот не поднимется.
if [[ ! -f .env ]]; then
  echo "❌ Нет .env — скопируй .env.example и впиши BOT_TOKEN"
  exit 1
fi
if ! grep -q '^BOT_TOKEN=.\+' .env; then
  echo "❌ В .env пустой BOT_TOKEN"
  exit 1
fi

echo "🧹 Чищу __pycache__…"
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -delete 2>/dev/null || true

echo "📦 Пакую проект…"
# Модели (.pkl) НЕ кладём: они обучаются на сервере из его же БД. Копировать их
# бессмысленно — SVD-матрица привязана к конкретным customer_id.
tar --exclude=".git" \
    --exclude=".venv" \
    --exclude="__pycache__" \
    --exclude="*.pyc" \
    --exclude=".DS_Store" \
    --exclude=".idea" \
    --exclude=".vscode" \
    --exclude=".claude" \
    --exclude="*.tar.gz" \
    --exclude="*.log" \
    --exclude="ml/models/*.pkl" \
    --exclude="iiko/IIKO_API.json" \
    --exclude="*.pdf" \
    -czf $ARCHIVE \
    api/ bot/ db/ data_gen/ ml/ \
    Dockerfile docker-compose.prod.yml requirements.txt .env

echo "📤 Заливаю на сервер…"
scp -q $ARCHIVE $SERVER_USER@$SERVER_IP:/tmp/
rm -f $ARCHIVE

echo "🔧 Разворачиваю…"
$SSH bash -s -- "$SEED" << 'REMOTE'
set -euo pipefail
SEED="$1"
REMOTE_DIR=/home/boostaurant
COMPOSE="docker compose -f docker-compose.prod.yml --profile bot"

# Docker может отсутствовать на свежем VPS.
if ! command -v docker >/dev/null 2>&1; then
  echo "🐳 Ставлю Docker…"
  curl -fsSL https://get.docker.com | sh
fi

mkdir -p "$REMOTE_DIR"
# Код перезаписываем, но pgdata живёт в docker volume — данные переживут деплой.
tar -xzf /tmp/boostaurant.tar.gz -C "$REMOTE_DIR"
rm -f /tmp/boostaurant.tar.gz
cd "$REMOTE_DIR"

# В .env лежит токен бота — читать его должен только владелец.
chmod 600 .env

echo "🏗  Собираю образ (scikit-surprise компилируется, это долго)…"
$COMPOSE build

echo "▶️  Поднимаю сервисы…"
$COMPOSE up -d

echo "⏳ Жду API…"
for i in $(seq 1 60); do
  if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    echo "   API отвечает."
    break
  fi
  sleep 2
done

if [[ "$SEED" == "true" ]]; then
  echo "🌱 Генерирую данные…"
  $COMPOSE exec -T app python -m data_gen.generate --reset
  echo "🧠 Обучаю модели…"
  $COMPOSE exec -T app python -m ml.train_svd --no-eval
  $COMPOSE exec -T app python -m ml.train_kmeans
  # Модели пишутся внутрь контейнера, а кэш lru_cache держит старые (их нет) —
  # перезапуск app заставляет его подхватить свежие .pkl.
  $COMPOSE restart app
  sleep 5
fi

echo ""
echo "📊 Статус:"
$COMPOSE ps
echo ""
curl -s http://localhost:8000/health || echo "⚠️  /health не ответил"
REMOTE

echo ""
echo "========================================"
echo "✅ Готово! Бот работает — пиши ему в Telegram."
echo ""
echo "   API наружу НЕ открыт (публичный IP не нужен)."
echo "   Чтобы дёргать его с локали:"
echo ""
echo "     ./deploy.sh --tunnel      → http://localhost:8000/docs"
echo ""
echo "   Логи:     ./deploy.sh --logs"
echo "   Погасить: ./deploy.sh --down"
if [[ "$SEED" == "false" ]]; then
  echo ""
  echo "   ⚠️  Данные не трогал. Первый деплой запускай с --seed"
fi
exit 0
