# HeadInspect backend v0.2

Фоновый job-based backend для массового аудита.

## API

### Health

```bash
curl http://127.0.0.1:8000/health
```

### Запустить аудит

```bash
curl -X POST http://127.0.0.1:8000/api/audits \
  -H "Content-Type: application/json" \
  -d '{"url":"https://moskostumer.ru"}'
```

Ответ:

```json
{
  "job_id": "...",
  "status": "queued",
  "status_url": "/api/audits/... ",
  "results_url": "/api/audits/.../results"
}
```

### Прогресс

```bash
curl http://127.0.0.1:8000/api/audits/JOB_ID
```

Статусы:

- `queued`
- `discovering`
- `running`
- `completed`
- `failed`

### Частичные / финальные результаты

```bash
curl http://127.0.0.1:8000/api/audits/JOB_ID/results
```

Результаты доступны уже во время `running`.

## Ограничения v0.2

- максимум 500 URL на аудит;
- 4 страницы одновременно внутри одного аудита;
- одновременно выполняется максимум 1 тяжёлый аудит;
- остальные задания ждут в памяти;
- завершённые задания хранятся 1 час;
- при перезапуске контейнера jobs теряются.

Это намеренный MVP. До публичного запуска ещё нужны:

- persistent job store;
- rate limit;
- reverse proxy + HTTPS;
- проверка OG images;
- ограничение очереди;
- экспорт XLSX.
