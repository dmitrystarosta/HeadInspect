# HeadInspect — отчёт о реализации: «Нет ошибок» и `check_reason`

Дата: 29 августа 2026. Продолжение работы после технического предложения
(`PROPOSAL_2026-08-29_v2_with_timeout_analysis.md`) — по вашему заданию
(`HeadInspect_Claude_task_updated.md`) код был изменён.

## Результаты тестов

```
Backend:  71 passed, 0 failed
Frontend: 52 passed, 0 failed
Итого:    123 passed, 0 failed
```

Как и раньше, backend прогнан через локальные заглушки fastapi/httpx/pydantic
(в этой среде нет сети для `pip install`) — пожалуйста, подтвердите тем же
`pytest` на реальном окружении. Frontend прогнан по-настоящему, штатным
`node --test`, без заглушек.

Полные команды:

```bash
# backend
cd backend && pip install -r requirements-dev.txt && pytest

# frontend
node --test tests/frontend/
```

Три новых теста я специально проверил на способность ловить регресс: временно
откатывал соответствующее исправление, убеждался, что тест падает, затем
возвращал исправление и убеждался, что тест снова проходит. Это сделано для:
`test_mass_access_blocked_still_triggers_block_detection` (главный риск всей
доработки), `home.js` zero-state тестов (3 из 4 тестов новой группы
`zero-state.test.mjs` действительно падают на старой логике), и старой
доработки `test_run_pages_stop_event_halts_further_fetches` из предыдущего
раунда (осталась нетронутой, по-прежнему проходит).

## Итоговая модель данных `check_reason`

Новое поле в `PageResult` (`backend/app/models.py`):

```python
CheckFailureReason = Literal["network", "timeout", "content_type", "access_blocked"]

class PageResult(BaseModel):
    ...
    check_failed: bool = False
    check_error: str | None = None      # человекочитаемый текст, как и раньше
    check_reason: CheckFailureReason | None = None   # НОВОЕ структурированное поле
```

Заполняется **только** когда `check_failed=True`, ровно один из четырёх
вариантов:

| `check_reason` | Когда | `status_code` | Пример `check_error` |
|---|---|---|---|
| `"network"` | DNS/соединение не установлено | `None` | `Не удалось проверить страницу: Cannot fetch https://...` |
| `"timeout"` | Не получен ответ за отведённое время (оба источника таймаута — внутренний httpx и внешний `PAGE_TIMEOUT`) | `None` | `Страница не ответила за 30 с` |
| `"content_type"` | Получен ответ, но это не HTML / слишком большой ответ | `None` | `...Unexpected content type: image/jpeg` |
| `"access_blocked"` | **Новое.** Получен HTTP-ответ 401/403/429 | **заполнен** (401/403/429) | `HTTP 403. Сервер запретил HeadInspect автоматический доступ... Заголовок страницы, которую вернул сервер: «Verification required».` |

Единственная причина, где `status_code` заполнен несмотря на
`check_failed=True`, — `access_blocked`: это осознанное решение, именно оно
позволяет Sitemap продолжать показывать «URL из sitemap недоступен: HTTP
403» вместо обобщённого «Без ответа» (см. ниже).

На фронтенде (`site/common.js::describeCheckReason`) поле превращается в
короткую подпись, показываемую **прямо в свёрнутой строке** списка «Без
ответа» (не только при раскрытии деталей):

```
"timeout"          → "Не ответила за 30 с"   (длительность вытаскивается из check_error, не захардкожена)
"access_blocked"   → заголовок страницы, если сервер его вернул (например "Verification required"),
                      иначе "HTTP {код}"
"content_type"      → "Не HTML"
"network"           → "Нет соединения"
```

## Что сделано по каждому пункту задания

### 1. Однозначное отображение «без ошибок»

**Файл:** `site/home.js::renderAuditModule`.

Было: `if (errors || warnings)` — общее условие на оба показателя, из-за чего
`errors === 0` при `warnings > 0` рендерило буквально «0 ошибок». Теперь
каждый показатель оценивается независимо, ровно как вы просили:

```js
const errorsText = errors ? `${errors} ${plural(...)}` : "Нет ошибок";
const warningsText = warnings ? `${warnings} ${plural(...)}` : "Нет предупреждений";
counts.innerHTML = `<strong${errors ? "" : ' class="module-ok"'}>${errorsText}</strong><small>${warningsText}</small>`;
```

Используется существующий класс `module-ok` (`color: var(--success)`),
`<strong>` уже жирный по умолчанию — новый CSS не понадобился. «0 ошибок»
теперь физически не может появиться в тексте ни при каком сочетании
errors/warnings. Аналогичная (более мягкая) несогласованность в тексте
абзаца модуля тоже выровнена под ту же независимую логику.

### 2–3. Единая категория «Без ответа» + причина + не путать с ошибками

**Backend, `backend/app/audit.py::analyze_page`:** при получении ответа со
статусом 401/403/429 — **не** вызываются `analyze_open_graph`/`analyze_meta`/
`analyze_schema` вообще. Страница помечается `check_failed=True,
check_reason="access_blocked"`, сохраняется реальный `status_code` и (только
как справочная деталь, не как основание для классификации) заголовок
страницы, если сервер его вернул. Проверено тестом, что при 401/403/429
счётчики вызовов анализаторов равны нулю.

**Frontend:** категория осталась одна — `status: "unavailable"` (вкладка
«Без ответа» не переименована и не размножена на несколько вкладок, как вы и
просили). Причина показывается двумя способами:
- **сразу в свёрнутой строке списка** — `describeCheckReason(page)`, что и
  было отдельно и явно запрошено («рядом с каждой такой страницей нужно
  показывать понятную причину», не только после клика);
- **полным предложением в раскрытых деталях** — `check_error` как есть.

### 4. `radov39.ru`: без эвристики, с проверенным фактическим `AUDIT_TIMEOUT`

Фактическое значение в текущем коде: **`AUDIT_TIMEOUT = 90.0` секунд**
(`backend/app/config.py`, строка 15) — нашёл именно в актуальном коде, не
опираясь на более старые версии. Как отмечал в предыдущем предложении, это
арифметически не вполне сходится с наблюдаемыми на проде 275 таймаутами (при
`PAGE_CONCURRENCY=4` это ~34 минуты суммарного ожидания) — рекомендую ещё раз
свериться с реальным прод-конфигом, если он отличается.

Эвристика вида «много timeout = блокировка» **не вводилась** — как и
согласовано. `check_reason="timeout"` **никак** не участвует в
`is_block_response` (детектор блокировки реагирует только на
`status_code ∈ {401, 403, 429}`, а у таймаута `status_code` всегда `None`).
Добавлен отдельный regрессионный тест
(`test_mass_timeouts_never_trigger_block_detection`), прогоняющий 50 URL (20
успешных + 30 таймаутов подряд) и проверяющий, что `blocked_mid_audit`
остаётся `False`. Детерминированная проверка бюджета времени из
предложения (раздел 3.4) **не реализовывалась** — в вашем задании она
осталась опциональной («можно использовать»), не обязательной для этого
этапа.

### 5. Структурированная причина вместо парсинга текста

Прошёл весь путь `check_error` от источника до отображения:

1. **Источник** — `backend/app/fetcher.py` бросает `HTTPException` с
   разным `status_code`/`detail` для сети/таймаута/типа контента;
   `backend/app/audit.py` перехватывает и **один раз** классифицирует через
   новую функцию `_classify_fetch_failure_reason`.
2. **Хранение** — `PageResult.check_reason` (models.py).
3. **API** — уходит в JSON как обычное поле `AuditResultsResponse.results[].check_reason`, без трансформаций.
4. **Frontend** — раньше `sitemap.js` был вынужден **разбирать `check_error`
   регэкспом** (`/Unexpected content type:\s*([^\s]+)/i`), чтобы отличить
   "не HTML" от прочих сбоев. Теперь маршрутизация идёт по
   `page.check_reason === "content_type"` — сам регэксп оставлен **только**
   для того, чтобы вытащить конкретный MIME-тип для текста сообщения
   (`"Не HTML: image/jpeg"`), а не для решения, к какой категории отнести
   страницу — это явное сокращение хрупкости, о котором вы просили.
5. Пользовательский текст (`check_error`) остался человекочитаемым и не
   тронут по смыслу — структурное поле добавлено **рядом**, не вместо него.

### 6. Критическая защита от регресса блокировки

`backend/app/jobs.py::_make_on_result` — единственное обязательное защитное
изменение:

```python
# Было:
is_block_response = not result.check_failed and result.status_code in BLOCK_DETECT_STATUS_CODES
# Стало:
is_block_response = result.status_code in BLOCK_DETECT_STATUS_CODES
```

Без этой правки все 401/403/429-страницы (теперь `check_failed=True`)
навсегда перестали бы засчитываться детектором массовой блокировки — самая
серьёзная скрытая опасность всей доработки. Заодно `BLOCK_DETECT_STATUS_CODES`
в `jobs.py` теперь **импортируется** из `audit.py`
(`ACCESS_BLOCKED_STATUS_CODES`), а не дублируется вторым независимым
списком — исключает возможность их будущего расхождения.

Существующая защита `stop_event` (двойная проверка — до и после занятия
слота семафора, `run_pages.worker` + `analyze_page`) **не менялась вообще** —
только подтверждена регрессионными тестами, что она всё ещё работает с
новой веткой `access_blocked` внутри `analyze_page`.

## Изменённые файлы

**Backend:**
- `backend/app/models.py` — добавлено поле `check_reason` и тип `CheckFailureReason`
- `backend/app/audit.py` — новая ветка `access_blocked` в `analyze_page`, функция `_classify_fetch_failure_reason`, `check_reason="timeout"` в `run_pages.worker`
- `backend/app/jobs.py` — критическая правка `is_block_response`, импорт `ACCESS_BLOCKED_STATUS_CODES` вместо дублирования
- `backend/tests/test_check_reason.py` — **новый файл**, 14 тестов
- `backend/tests/test_jobs_block_detection.py` — фикстура `make_result` приведена в соответствие с реальным поведением `analyze_page` (была одним из источников риска "тесты живут отдельно от кода" — как и в прошлый раз)
- `backend/tests/test_job_lifecycle.py` — то же для фикстуры одиночного 403
- `backend/tests/test_job_isolation.py` — фикстура job A обновлена + новый тест на изоляцию `check_reason` между job

**Frontend:**
- `site/home.js` — независимая оценка errors/warnings (item 1)
- `site/common.js` — новая функция `describeCheckReason`, экспортирована в `HI`
- `site/app.js`, `site/meta.js`, `site/schema.js` — `mapApiRow` использует `describeCheckReason` для `message`, текст таймаута строится через `check_reason`, а не `startsWith`
- `site/sitemap.js` — `access_blocked`-страницы явно обходят общую `check_failed`-ветку и проваливаются в уже существующую, верную логику по `status_code`; content-type тоже теперь маршрутизируется по `check_reason`, а не только по регэкспу
- `tests/frontend/helpers.mjs` — тестовый DOM-стенд расширен (селектор `.audit-module[data-module="..."]`, составные селекторы вида `.class tag`) для проверки `renderAuditModule`
- `tests/frontend/common.test.mjs` — 5 новых тестов на `describeCheckReason`
- `tests/frontend/map-api-row.test.mjs` — обновлены 2 существующих теста под новые фикстуры + 4 новых теста (access_blocked с/без заголовка, sitemap access_blocked-маршрутизация, отсутствие смешивания причин между страницами)
- `tests/frontend/zero-state.test.mjs` — **новый файл**, 4 теста на независимое отображение нулевых состояний
- `tests/frontend/job-isolation.test.mjs` — без изменений в этой сессии (существующие тесты продолжают проходить)

## Что не менялось (намеренно)

- Название вкладки «Без ответа» — оставлено как есть, по вашему явному указанию (не «Не проверено»).
- `stop_event`, `completed_partial`, сохранение partial results, job isolation, подсчёт уникальных URL — не тронуты, только защищены дополнительными тестами.
- Детерминированная проверка бюджета времени для `radov39.ru`-сценария — не реализована (опционально по заданию).
- Excel-экспорт — по-прежнему не реализован в кодовой базе (заглушка `alert(...)` на всех 4 модулях), менять нечего.

## Риски и что стоит проверить на проде

1. **Сдвиг статистики** — на сайтах с отдельными 403-страницами
   `errors_found` уменьшится, `failed_checks` вырастет на ту же величину
   (страницы больше не порождают ложные "Нет og:title"). Это ожидаемо, не
   баг.
2. **`AUDIT_TIMEOUT`** — расхождение с прод-наблюдениями `radov39.ru` (см.
   пункт 4) стоит явно сверить с реальным конфигом перед тем, как делать
   выводы о производительности.
3. Рекомендую после деплоя вручную повторить `zipkran.ru` и убедиться, что
   Open Graph/Meta/Schema для страниц вида `Verification required` больше не
   показывают «Нет og:title» и подобные ошибки, а показывают короткую метку
   с заголовком страницы прямо в списке.
