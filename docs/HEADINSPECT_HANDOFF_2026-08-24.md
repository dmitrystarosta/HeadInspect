# HeadInspect --- handoff 2026-08-24

## Назначение

Актуальная точка передачи проекта HeadInspect на 24 августа 2026 года
для продолжения работы в новом чате.

Рекомендуемая первая фраза: \> Продолжаем HeadInspect. Начни с
`HEADINSPECT_HANDOFF_2026-08-24.md`.

## 1. Проект

HeadInspect --- инструмент массового технического аудита сайтов.

Домены: `headinspect.ru`, `headinspect.com`. GitHub:
`dmitrystarosta/HeadInspect`. Telegram: `t.me/headinspect`. ВКонтакте:
`vk.ru/headinspect`.

Основные направления: Open Graph, Meta, Canonical, Schema, Images,
Sitemap; в дальнейшем --- единый site-wide audit до 500 URL и
Excel-отчёт. Первый рынок --- русскоязычный, проект развивается с
расчётом на SEO.

## 2. Архитектура

Frontend статический, находится в `/site`, публикуется через GitHub
Pages и `headinspect.ru`.

Основные страницы: - `site/index.html` - `site/open-graph/index.html` -
`site/meta/index.html` - `site/canonical/index.html` -
`site/schema/index.html` - `site/images/index.html` -
`site/sitemap/index.html` - `site/404.html`

Backend находится в `/backend`, FastAPI работает на VPS в Docker. API:
`https://api.headinspect.ru`. Health endpoint:
`https://api.headinspect.ru/health`.

На 24.08.2026 healthcheck:

``` json
{"status":"ok","service":"headinspect-api","version":"0.3.0"}
```

Контейнер: `headinspect-api`, порт `127.0.0.1:8000 -> 8000`.

## 3. Git --- текущее состояние

Актуальный GitHub HEAD и VPS HEAD:

``` text
df0fcbd
```

Важный backend-коммит:

``` text
da877d6 Improve OG image fetching for CDN responses
```

Проверено:

``` powershell
git diff da877d6..HEAD -- backend/app/fetcher.py backend/app/analyzers/open_graph.py
```

Вывод пустой: исправленные backend-файлы из `da877d6` сохранены в
текущем HEAD.

### Домашний компьютер

Репозиторий:

``` text
C:\Users\Дмитрий\Desktop\HeadInspect
```

24.08.2026 он отставал на 41 коммит. Выполнены `git fetch origin` и
`git pull --ff-only`. Теперь синхронизирован с `df0fcbd`, рабочее дерево
чистое.

### VPS

До обновления сервер был на `0182b79 Add Open Graph image validation`.
После `git fetch origin` выяснилось отставание на 52 коммита. Выполнен
`git pull --ff-only`. Теперь VPS также на `df0fcbd`, рабочее дерево
чистое.

## 4. VPS

IP:

``` text
138.16.191.163
```

Ubuntu 24.04.4 LTS. Рабочий пользователь: `headinspect`. Прямой SSH root
отключён (`PermitRootLogin no`). Репозиторий: `~/headinspect`. Backend:
`~/headinspect/backend`.

24.08.2026 выполнено:

``` bash
cd ~/headinspect/backend
docker compose up -d --build
docker compose ps
curl https://api.headinspect.ru/health
```

Сборка успешна. Предупреждение о Bake/buildx не помешало сборке.

## 5. Исправление og:image / Tilda CDN

При аудите:

``` text
https://obertaeva.ru/shou-na-vode
```

для изображения:

``` text
https://static.tildacdn.com/stor6365-3638-4366-a631-623664646365/-/resize/504x/56287086.jpg
```

иногда получалось:

``` text
image HTTP: 204
image MIME: —
image format: —
image size: —
image weight: 0 B
```

и рекомендация «Не удалось определить формат или размеры».

Backend уже использовал GET. CDN мог возвращать `204 No Content`, а код
воспринимал статус `<400` как успешную загрузку и анализировал пустое
тело.

Исправлены: - `backend/app/fetcher.py` -
`backend/app/analyzers/open_graph.py`

Коммит: `da877d6`.

До развёртывания правки тот же URL иногда уже отвечал нормально, что
подтвердило плавающий характер CDN-проблемы.

После обновления VPS и пересборки backend повторные проверки на домашнем
ПК и телефоне дали:

``` text
image HTTP: 200
image MIME: image/jpeg
image format: JPEG
image size: 504×378
image weight: 32 KB
```

Превью отображается. Старая ошибка отсутствует. Остаётся корректная
рекомендация «Маленькое og:image: 504×378».

Статус: исправление развёрнуто, текущие проверки успешны.

## 6. SSH --- домашний компьютер

Ключ:

``` text
C:\Users\Дмитрий\.ssh\headinspect_vps
```

Публичный ключ:

``` text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDppzmuuKukmbFJ1C6Blyvr2i+7YOR2ufQW0S7ZVo5hW headinspect-vps
```

Удобный alias уже настроен и работает:

``` powershell
ssh headinspect
```

Приватный ключ защищён passphrase. Passphrase ключа --- не пароль VPS.

## 7. SSH --- дачный ноутбук

Windows-пользователь: `C:\Users\Антон`.

Ключ:

``` text
C:\Users\Антон\.ssh\id_ed25519
```

Правильный публичный ключ:

``` text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGdPzsURwV6FUvOih4M5TkIB5XG0uRZXdWOLYbMfzl// dacha-headinspect
```

Fingerprint:

``` text
SHA256:f5RLypFUI/H+9SVdymVXmyHMvNFXQFOt8/p2wKkVo3o
```

### Причина прежнего отказа

21.08.2026 ключ добавлялся через VNC VDSina, где не работал clipboard, и
был набран вручную. В серверный файл попала ошибочная строка с
фрагментом:

``` text
...G0uRZXdwOLYoMfzl//
```

вместо:

``` text
...G0uRZXdWOLYbMfzl//
```

Поэтому sshd закономерно отвергал ключ. Настройки sshd, права файлов и
аккаунт `headinspect` были исправны.

### Исправление

24.08.2026 через рабочий домашний SSH файл `~/.ssh/authorized_keys`
приведён к двум правильным ключам. Перед этим создан:

``` text
~/.ssh/authorized_keys.bak
```

Текущее содержимое `authorized_keys`:

``` text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDppzmuuKukmbFJ1C6Blyvr2i+7YOR2ufQW0S7ZVo5hW headinspect-vps
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGdPzsURwV6FUvOih4M5TkIB5XG0uRZXdWOLYbMfzl// dacha-headinspect
```

Права: `.ssh` --- 700, `authorized_keys` --- 600.

На дачном ноутбуке создан `C:\Users\Антон\.ssh\config`:

``` text
Host headinspect-vps
    HostName 138.16.191.163
    User headinspect
    IdentityFile ~/.ssh/id_ed25519
```

Notepad сначала сохранил его как `config.txt`; файл переименован в
`config`.

Теперь работает:

``` powershell
ssh headinspect-vps
```

Вход по ключу проверен успешно, без пароля пользователя VPS.

## 8. Временный sshd на 2222

Во время диагностики запускался временный sshd на порту 2222. 24.08.2026
проверено:

``` bash
ps aux | grep '[s]shd.*2222'
```

Вывода нет. Временный sshd не запущен.

## 9. Индексация

HeadInspect добавлен в Google Search Console. Принято решение закрывать
от индексации незавершённые инструменты через `robots.txt`. Актуальный
`robots.txt` находится в текущем репозитории. Не открывать заглушки
поисковикам до готовности.

## 10. Последние frontend-изменения

В актуальном репозитории есть новая `404.html`,
favicon/apple-touch-icon, оптимизированные WebP-логотипы HeadInspect и
Белого списка.

Обновлялись `index.html`, страницы инструментов, `styles.css`, `app.js`,
`home.js`, `robots.txt`.

Недавние решения: - Images добавлена в навигацию; - GitHub убран из
верхнего меню, оставлен в footer; - ВКонтакте добавлен в footer; -
создана 404; - тяжёлые PNG оптимизировались в WebP; - улучшалась
мобильная версия; - рекомендации title/description получили цветовую
логику; - desktop/mobile продолжают унифицироваться.

## 11. Технические принципы

-   Site-wide crawl: максимум 500 страниц.
-   Начинать с robots.txt / sitemap.
-   При отсутствии sitemap показывать понятную ошибку/рекомендацию.
-   Результаты преимущественно списком, а не тяжёлыми карточками.
-   Индикация: зелёный / жёлтый / красный.
-   Backend отделён от GitHub Pages и работает на VPS.
-   Незавершённые страницы не индексировать.
-   HeadInspect развивается как публичный SEO-инструмент, а не только
    внутренний сервис.

## 12. Следующие задачи

1.  Продолжить тестирование Open Graph analyzer на разных сайтах/CDN.
2.  Проверять redirect изображений, 204/empty body, неверный
    Content-Type, WebP/AVIF, большие изображения, относительные URL,
    query string, CDN/anti-bot.
3.  Продолжить улучшение OG UX и рекомендаций.
4.  Вернуться к другим ошибкам/особенностям аудита `obertaeva.ru`, если
    они остаются помимо исправленного og:image.
5.  Развивать Meta / Canonical / Schema / Images / Sitemap.
6.  Постепенно перейти к полноценному site-wide audit.
7.  Для других ноутбуков создавать отдельные SSH-ключи; приватные ключи
    между устройствами не копировать.

## 13. Git workflow

Перед работой на компьютере:

``` powershell
git fetch origin
git status
```

Если дерево чистое и ветка отстаёт:

``` powershell
git pull --ff-only
```

На VPS после backend-изменений:

``` bash
cd ~/headinspect
git fetch origin
git status
git pull --ff-only

cd ~/headinspect/backend
docker compose up -d --build
docker compose ps
curl https://api.headinspect.ru/health
```

Не делать pull вслепую при локальных изменениях.

## 14. Безопасность

Не помещать в Git: - приватные SSH-ключи; - пароли; - токены; - API
secrets.

Root SSH остаётся отключённым. Рабочий пользователь --- `headinspect`.
VNC использовать только как аварийный доступ.

## 15. Точка продолжения

На конец 24.08.2026: - GitHub HEAD: `df0fcbd`; - домашний репозиторий
синхронизирован; - VPS синхронизирован; - backend пересобран; - API
0.3.0 работает; - `/health` отвечает `ok`; - исправление Tilda CDN /
og:image развёрнуто; - повторные проверки `obertaeva.ru` на ПК и
телефоне успешны; - домашний SSH: `ssh headinspect`; - дачный ноутбук:
`ssh headinspect-vps`; - `authorized_keys` содержит два правильных
публичных ключа; - временного sshd на 2222 нет; - VNC для обычной работы
больше не требуется.

Начать новый чат сообщением: \> **Продолжаем работу над HeadInspect.
Начни с `HEADINSPECT_HANDOFF_2026-08-24.md`. Сначала прочитай текущее
состояние и продолжим с точки, описанной в разделе «Точка
продолжения».**

## 8. Обновление 24.08.2026 — privacy, аналитика, SEO/OG и сохранение audit job

К концу сессии рабочими являются два site-wide модуля: Open Graph и Meta. Общий аудит на главной использует один backend job, а специализированные страницы открывают тот же результат без повторного обхода.

### Аналитика и privacy

На сайт добавлены:

- `/privacy/` — политика обработки персональных данных;
- единый consent-механизм `site/consent.js`;
- Яндекс Метрика, счётчик `111906427`;
- Метрика не загружается до нажатия «Принять»;
- после согласия выбор сохраняется в localStorage и счётчик загружается автоматически;
- работу проверили в production через Firefox DevTools / Network: до согласия запросов к `mc.yandex.ru` нет, после согласия `tag.js?id=111906427` загружается;
- единый `footer.js` используется на рабочих страницах, страницах «скоро», `/privacy/` и 404.

### SEO и Open Graph собственных страниц HeadInspect

Для `/`, `/open-graph/`, `/meta/`, `/privacy/` подготовлены индивидуальные:

- `title`;
- `meta description`;
- `meta keywords`;
- canonical;
- `og:title`;
- `og:description`;
- `og:url`;
- `og:type`;
- `og:site_name`;
- `og:locale`;
- `og:image` и его параметры.

Добавлен общий файл `site/og-image.jpg`, размер 1200×630. Это нужно в том числе для самопроверки HeadInspect: рабочие страницы не должны сами давать ошибку «Нет og:image».

404 сохраняет `noindex, follow`, но также получает заполненные Meta/OG-поля для единого технического оформления.

### Сохранение job в навигации

Исправляется обнаруженный мобильный сценарий: после запуска общего аудита бургер-меню раньше содержало обычные `/open-graph/` и `/meta/`, из-за чего переход сбрасывал `job`.

Требуемое поведение:

- после появления `job` главная динамически дописывает `job` и `url` в ссылки верхнего меню;
- логотип HeadInspect при активном аудите возвращает на главную с тем же `job`;
- Open Graph и Meta сохраняют контекст в навигации;
- параметры заранее поддерживаются для `/canonical/`, `/schema/`, `/images/`, `/sitemap/`, чтобы будущие модули подключались к тому же audit job;
- страницы «скоро», открытые с `?job=...&url=...`, сохраняют этот контекст через общий `shell.js`.

### Политика

В тексте политики собственные адреса HeadInspect приводятся как кликабельные абсолютные HTTPS-ссылки. Ссылки на Яндекс в текст политики специально не добавляются.

Следующий функциональный модуль после фиксации этого состояния — Canonical.
