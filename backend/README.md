# HeadInspect backend v0.3

Open Graph MVP, уже рассчитанный на публичное подключение после reverse proxy/rate limit.

## Что проверяет OG

Для каждой страницы:

- `og:title`
- `og:description`
- `og:url`
- `og:type`
- `og:image`
- несколько `og:image`
- доступность `og:image`
- HTTP-статус картинки
- Content-Type
- реальный формат изображения
- реальный вес
- реальные ширина и высота
- маленькое изображение
- нестандартный размер относительно 1200×630
- несовпадение `og:image:width/height` с фактическими размерами

`og:image` скачивается тем же безопасным fetcher:
- только HTTP/HTTPS;
- только порты 80/443;
- DNS/IP проверка;
- блокировка private/local/link-local/reserved адресов;
- повторная проверка каждого redirect;
- максимум 6 MB на изображение.

## Jobs API

### Запуск

```bash
curl -X POST http://127.0.0.1:8000/api/audits \
  -H "Content-Type: application/json" \
  -d '{"url":"https://moskostumer.ru"}'
```

### Статус

```bash
curl http://127.0.0.1:8000/api/audits/JOB_ID
```

### Результаты

```bash
curl http://127.0.0.1:8000/api/audits/JOB_ID/results
```

## Ограничения

- до 500 страниц;
- 4 страницы параллельно;
- один тяжёлый аудит одновременно;
- jobs пока хранятся в RAM и теряются при restart контейнера;
- до публичного запуска добавить rate limiting и reverse proxy + HTTPS.

## Тесты

```bash
pip install -r requirements-dev.txt
pytest
```

См. также `../CHANGELOG.md` за 2026-08-28 — там описано, какие тесты
покрывают что.

