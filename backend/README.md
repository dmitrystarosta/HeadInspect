# HeadInspect backend v0.1

Первый рабочий серверный каркас HeadInspect.

Уже есть: FastAPI, `/health`, `/api/audit`, robots.txt, sitemap и sitemap-index,
Open Graph-анализатор, базовое извлечение title/description, Docker и базовая SSRF-защита.

На первом живом тесте лимит намеренно установлен в 20 URL. После проверки CPU/RAM
и поведения на реальных сайтах поднимем до 500 и добавим фоновые задания, прогресс,
rate limiting, OG image analysis, дубли и XLSX.

Запуск:

    cd backend
    docker compose up -d --build

Проверка:

    curl http://127.0.0.1:8000/health

Пример:

    curl -X POST http://127.0.0.1:8000/api/audit \
      -H "Content-Type: application/json" \
      -d '{"url":"https://example.com"}'

Порт 8000 опубликован только на 127.0.0.1. Позже перед API поставим reverse proxy
на `api.headinspect.ru` с HTTPS.
