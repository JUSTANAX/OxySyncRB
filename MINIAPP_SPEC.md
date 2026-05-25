# OxySync Bot — Полная техническая документация для Mini App

> Версия бота: **v1.5.4**  
> Стек: Python 3.11, aiogram 3.7, SQLite, aiohttp  
> Бот **single-user** — все события от чужих Telegram ID игнорируются через middleware

---

## 1. Архитектура

```
bot.py                  — точка входа, фоновые задачи, polling
config.py               — переменные окружения
database.py             — вся работа с SQLite (autocommit, single connection)
handlers/
  start.py              — главный экран, настройки, уведомления
  faceunlock.py         — Auto-Unlock-Face (ZeroPoint)
  autopilot.py          — Авто-пилот (AutoTradeToMain)
api/
  accountsops.py        — клиент к accountops.org
  faceunlock.py         — клиент к zeropoint.to
keyboards.py            — inline-клавиатуры
state_cache.py          — хранит message_id для live-редактирования статистики
charts.py               — генерация графиков (Pillow, не используется в UI)
```

---

## 2. База данных (SQLite)

### `panels`
| Поле | Тип | Описание |
|---|---|---|
| user_id | INTEGER PK | Telegram ID пользователя |
| api_key | TEXT | API ключ AccountsOps |
| connected_at | TEXT | Дата подключения |

### `alert_thresholds`
| Поле | Тип | Описание |
|---|---|---|
| user_id | INTEGER PK | |
| threshold | INTEGER | Порог активных аккаунтов |
| enabled | INTEGER | 1 = включено |
| last_notified | TEXT | Время последнего уведомления |
| triggered | INTEGER | 1 = порог уже сработал (ждём восстановления) |

### `zp_keys`
| Поле | Тип | Описание |
|---|---|---|
| user_id | INTEGER PK | |
| api_key | TEXT | API ключ ZeroPoint |

### `zp_jobs`
| Поле | Тип | Описание |
|---|---|---|
| user_id | INTEGER PK | |
| job_id | TEXT | ID активной задачи face unlock |
| notified | INTEGER | 1 = уже уведомили о завершении |
| added_at | TEXT | Время создания |

### `auto_unlock`
| Поле | Тип | Описание |
|---|---|---|
| user_id | INTEGER PK | |
| enabled | INTEGER | Авто-цикл вкл/выкл |
| interval_hours | REAL | Интервал (пресеты: 1, 2, 3, 4, 6 часов) |
| last_run_at | TEXT | Последний запуск |

### `autopilot_config`
| Поле | Тип | Default | Описание |
|---|---|---|---|
| user_id | INTEGER PK | | |
| main_account | TEXT | | Username основного аккаунта (принимает петов) |
| config_id | INTEGER | | ID трейд-конфига (применяется когда аккаунт получил пета) |
| farm_config_id | INTEGER | | ID фарм-конфига (применяется пока аккаунт фармит) |
| running | INTEGER | 0 | Запущен ли сейчас |
| started_at | TEXT | | UTC время запуска |
| check_interval | INTEGER | 30 | Секунд между проверками инвентарей |
| stuck_timeout | INTEGER | 10 | Минут до возврата зависшего аккаунта в фарм |
| last_checked_at | TEXT | | Последняя обработка цикла |
| trades_done | INTEGER | 0 | Счётчик завершённых трейдов за сессию |
| max_traders_per_server | INTEGER | 10 | Макс. кол-во одновременно торгующих аккаунтов на один VIP-сервер |

### `autopilot_pets`
| Поле | Тип | Описание |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| user_id | INTEGER | |
| pet_id | TEXT | Полный ID пета (напр. `soggy_spring_2026_strawberry_shortcake_ducky`) |

UNIQUE(user_id, pet_id)

### `autopilot_queue`
| Поле | Тип | Описание |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| user_id | INTEGER | |
| account_id | TEXT | ID аккаунта в AccountsOps |
| username | TEXT | Username аккаунта |
| status | TEXT | `farming` / `trading` / `stuck` |
| activated_at | TEXT | UTC когда аккаунт перешёл в статус `trading` (для stuck-детектора) |

### `pet_snapshots`
| Поле | Тип | Описание |
|---|---|---|
| user_id | INTEGER | |
| pet_kind | TEXT | Тип пета |
| quantity | INTEGER | Количество |
| recorded_at | TEXT | UTC (формат `YYYY-MM-DD HH:MM:00`) |

PK(user_id, pet_kind, recorded_at). Хранит историю 8 дней для расчёта фарм-статистики.

### `watched_pets`
| Поле | Тип | Описание |
|---|---|---|
| user_id | INTEGER | |
| filter_text | TEXT | Строка фильтра (case-insensitive substring) |

Список петов для отображения на главном экране статистики. Жёстко задан в `handlers/start.py`.

---

## 3. Внешние API

### AccountsOps
**Base URL:** `https://accountops.org`  
**Auth:** заголовок `X-Api-Key: {key}`  
**Retry:** 3 попытки + exponential backoff  
**Chunking:** `/api/accounts/enable` — по 50 usernames за запрос

| Метод | Endpoint | Тело запроса | Ответ |
|---|---|---|---|
| GET | `/api/dashboard` | — | `{active_count, passive_count, unstable_count, queue_count, joining_count, connected_count}` |
| GET | `/api/trackstats/accounts` | — | `[{id, username, ...}]` или `{accounts: [...]}` |
| GET | `/api/trackstats/accounts/{id}/pets` | — | `[{pet_kind, quantity, is_egg}]` |
| PUT/PATCH/POST | `/api/accounts/enable` | `{usernames: [...], enabled: bool}` | — |
| POST | `/api/accounts/config` | `{usernames: [...], config_id: int}` | — |
| GET | `/api/player-configs` | — | `[{id, name}]` |
| POST | `/api/devices/accounts` | `{tag: "status:face"}` | `{devices: [{accounts: [{username, cookie, password}]}]}` |

**Поле `active_count`** в dashboard — главная метрика фермы.  
**`pet_kind`** пример: `soggy_spring_2026_strawberry_shortcake_ducky`

### ZeroPoint (Face Unlock)
**Base URL:** `https://zeropoint.to/api/faceunlock-api`  
**Auth:** заголовок `X-API-Key: {key}`

| Метод | Endpoint | Тело | Ответ |
|---|---|---|---|
| GET | `/balance` | — | `{effective: float, reserved: float}` |
| POST | `/submit` | `{accounts: "строка\nаккаунтов"}` | `{job_id, total_accounts, paid_accounts_count, estimated_cost}` |
| GET | `/status/{job_id}` | — | `{status, total_accounts, processed, successful, failed, other_failed, result_files: [...]}` |
| POST | `/cancel/{job_id}` | — | — |
| GET | `/download/{job_id}/{filename}` | — | bytes |

**Статусы задачи:** `pending` / `processing` / `completed` / `failed` / `cancelled`

**Формат аккаунтов для submit:**
- `username:password:.ROBLOSECURITY_value` (если есть логин/пароль)
- `.ROBLOSECURITY_value` (только cookie, без префикса `.ROBLOSECURITY=`)

---

## 4. Фоновые задачи (bot.py)

| Задача | Интервал | Описание |
|---|---|---|
| `alert_loop` | каждые 300 сек | Проверяет пороги активных аккаунтов |
| `auto_unlock_loop` | каждые 1800 сек | Запускает face unlock для юзеров с авто-циклом |
| `job_poller_loop` | каждые 30 сек | Проверяет статус ZP-задач, шлёт результат |
| `stats_refresh_loop` | каждые 300 сек | Редактирует сообщение со статистикой |
| `autopilot_transfer_loop` | каждые 5 сек (глобально) | Обрабатывает каждого юзера по его `check_interval` |

---

## 5. Экраны и логика бота

### 5.1 Главный экран — Статистика

**Что показывается:**
- Статус подключения AccountsOps (🟢/🔴)
- Счётчики: ✅ активных | 💤 пассивных | ⚠️ нестабильных
- Баланс ZeroPoint (если ключ задан): `$X.XX` + резерв
- Список петов по фильтрам из `WATCHED_PETS` с количеством и динамикой за 1ч / 12ч / 24ч / 3д / 7д

**Кнопки:**
- 🔄 Обновить → обновляет сообщение (`refresh`)
- 🔔 Уведомления → экран уведомлений (`alerts`)
- 🔧 Настройки → экран настроек (`settings`)
- 🤖 Автоматизация → меню автоматизации (`automation`)

**Live-обновление:** сообщение автоматически редактируется фоновой задачей каждые 5 минут.

---

### 5.2 Настройки

**Что показывается:** кнопка смены API ключа AccountsOps.

**Кнопки:**
- 🔑 Сменить/Подключить API ключ → ввод текстом, проверяется через `GET /api/dashboard`

---

### 5.3 Уведомления о активных аккаунтах

**Логика:**
- Задаётся числовой порог (напр. `50`)
- Фоновая задача каждые 5 минут проверяет `active_count`
- Если `active_count < threshold` и `triggered = 0` → шлёт алерт, ставит `triggered = 1`
- Если `active_count >= threshold` и `triggered = 1` → шлёт уведомление о восстановлении, снимает флаг
- Можно включить/выключить не меняя порог

**Данные:**
- `threshold` — INTEGER
- `enabled` — bool
- `triggered` — bool (внутреннее состояние)

**Кнопки:**
- ✏️ Задать/изменить порог → ввод числа
- ✅/❌ Включено/Выключено → toggle

---

### 5.4 Автоматизация — меню

Две кнопки:
- 🔓 Auto-Unlock-Face
- 🤖 Авто-пилот

---

### 5.5 Auto-Unlock-Face

**Назначение:** снять Face ID с аккаунтов через сервис ZeroPoint.

**Что показывается:**
- Баланс ZeroPoint: `$X.XX` (резерв если > 0)
- Статус активной задачи (если есть): статус, прогресс `X/Y (N%)`, разблокировано/ошибок
- Кнопки скачивания файлов результата (после завершения)
- Авто-цикл вкл/выкл + текущий интервал (1ч/2ч/3ч/4ч/6ч — цикличное переключение)

**Кнопки по статусу задачи:**

| Статус | Кнопки |
|---|---|
| Нет задачи | 🔓 Запустить разблокировку |
| `pending` / `processing` | 🔄 Обновить, ❌ Отменить |
| `completed` | 📥 Скачать файлы, 🔄 Обновить, 🔓 Новый запуск |
| `failed` / `cancelled` | 🔄 Обновить, 🔓 Новый запуск |

**Процесс запуска:**
1. Берём аккаунты с тегом `status:face` через `POST /api/devices/accounts`
2. Форматируем cookie строки
3. Показываем подтверждение: "Найдено X аккаунтов. Отправить?"
4. После подтверждения: `POST /submit` → сохраняем `job_id` в БД

**Авто-цикл:**
- Если включён, фоновая задача каждые 30 мин (или по интервалу) автоматически запускает новый цикл
- Если задача уже активна — не дублирует

**Уведомление о завершении:** фоновый поллер каждые 30 сек проверяет статус, при завершении шлёт итог:
```
✅/❌/🚫 Auto-Unlock-Face — завершена/ошибка/отменена
📊 Всего: N
✅ Разблокировано: N
❌ Face ID не снят: N
⚠️ Прочие ошибки: N (если > 0)
```

---

### 5.6 Авто-пилот (AutoTradeToMain)

**Назначение:** автоматически торговать петами. Аккаунты фармят питомцев, при получении нужного пета переключаются в режим трейда и передают пета основному аккаунту, затем возвращаются в фарм.

#### Настройки авто-пилота

| Параметр | Тип | Default | Описание |
|---|---|---|---|
| `main_account` | username | — | Основной аккаунт — принимает петов, всегда включён во время работы |
| `pet_ids` | список строк | — | Один или несколько pet_kind для отслеживания |
| `config_id` | INTEGER | — | Трейд-конфиг — применяется когда аккаунт получил пета |
| `farm_config_id` | INTEGER | — | Фарм-конфиг — применяется пока аккаунт фармит |
| `check_interval` | 10–300 сек | 30 | Как часто проверять инвентари аккаунтов |
| `stuck_timeout` | 1–60 мин | 10 | Сколько ждать трейда до возврата аккаунта в фарм |

#### Запуск авто-пилота

1. Отключить все аккаунты разом
2. Включить `main_account`
3. Получить все аккаунты (`/api/trackstats/accounts`)
4. Исключить: `main_account`, аккаунты с тегом `status:face`, аккаунты с тегом `status:dead`
5. Если задан `farm_config_id` — применить его на все оставшиеся аккаунты
6. Включить все оставшиеся аккаунты
7. Добавить все в `autopilot_queue` со статусом `farming`

#### Цикл (каждые `check_interval` секунд)

**Шаг 1 — проверка `trading` аккаунтов (передали ли пета?):**
- Для каждого аккаунта со статусом `trading`:
  - Запросить `/api/trackstats/accounts/{id}/pets`
  - Если ни одного из `pet_ids` нет → пет передан:
    - Применить `farm_config_id` (если задан)
    - disable → enable (рестарт аккаунта)
    - Статус → `farming`
    - Инкрементировать `trades_done`

**Шаг 2 — проверка зависших `trading` аккаунтов:**
- Если аккаунт в статусе `trading` и `activated_at` > `stuck_timeout` минут назад:
  - Применить `farm_config_id` (если задан)
  - disable → enable (рестарт)
  - Статус → `farming`
  - Уведомить пользователя о возврате в фарм

**Шаг 3 — проверка `farming` аккаунтов (получили ли пета?):**
- Для каждого аккаунта со статусом `farming`:
  - Запросить `/api/trackstats/accounts/{id}/pets`
  - Если есть хотя бы один из `pet_ids`:
    - Применить `config_id` (трейд-конфиг, если задан)
    - disable → enable (рестарт)
    - Статус → `trading` (фиксируется `activated_at`)

#### Остановка авто-пилота

- Отключить все `farming` аккаунты
- Отключить все `trading` аккаунты
- Отключить `main_account`
- Очистить очередь
- `running = 0`

#### Статус авто-пилота (отображение в реальном времени)

```
▶️ Запущен · Фармит: X · Торгует: Y · Сделок: Z
```
или
```
⏹ Остановлен
```

#### Кнопки авто-пилота

| Кнопка | Действие |
|---|---|
| 👤 {main_account} | Задать основной аккаунт (ввод текстом) |
| 🦆 Петы: N | Открыть список петов (добавить/удалить) |
| 🔄 Трейд конфиг: {id} | Выбрать трейд-конфиг из списка AccountsOps |
| 🌾 Фарм конфиг: {id} | Выбрать фарм-конфиг из списка AccountsOps |
| ⏱ Проверка: Xс | Задать интервал проверки (10–300 сек) |
| ⏰ Стак-таймаут: Xм | Задать стак-таймаут (1–60 мин) |
| ▶️ Запустить / ⏹ Остановить | Старт/стоп |
| 🔄 Обновить | Обновить страницу (только когда запущен) |

---

## 6. Навигация (callback_data)

```
back                    — назад на главный экран
refresh                 — обновить главный экран
noop                    — ничего не делать (для кнопок-заглушек)
settings                — экран настроек
  set_key               — ввод нового API ключа AccountsOps
alerts                  — экран уведомлений
  alert_set             — задать порог
  alert_toggle          — вкл/выкл уведомления
automation              — меню автоматизации
  face_unlock           — экран Auto-Unlock-Face
    fu_run              — запустить разблокировку (→ подтверждение)
    fu_confirm          — подтвердить запуск
    fu_refresh          — обновить статус
    fu_cancel           — отменить задачу
    fu_auto_toggle      — вкл/выкл авто-цикл
    fu_interval_cycle   — переключить интервал (1ч→2ч→3ч→4ч→6ч→1ч)
    fu_set_key          — ввод ZeroPoint API ключа
    fu_dl:{filename}    — скачать файл результата
  autopilot             — экран авто-пилота
    ap_refresh          — обновить страницу
    ap_set_main         — задать основной аккаунт (ввод текстом)
    ap_set_pet          — открыть список петов
      ap_add_pet        — добавить пет (ввод текстом)
      ap_del_pet:{id}   — удалить пет по row_id
    ap_set_config       — выбрать трейд-конфиг из списка
      ap_cfg:{id}       — применить выбранный трейд-конфиг
    ap_set_farm_config  — выбрать фарм-конфиг из списка
      ap_farm_cfg:{id}  — применить выбранный фарм-конфиг
    ap_set_interval     — задать интервал проверки (10–300 сек, ввод текстом)
    ap_set_stuck        — задать стак-таймаут (1–60 мин, ввод текстом)
    ap_start            — запустить авто-пилот
    ap_stop             — остановить авто-пилот
```

---

## 7. HTTP API Mini App-сервера

Рядом с ботом поднимается HTTP-сервер (FastAPI / aiohttp). Mini App обращается к нему напрямую.  
Telegram Mini App передаёт `initData` → сервер проверяет подпись и извлекает `user_id`.  
Все эндпоинты ниже — относительно базового URL сервера.

---

### GET /api/dashboard

Главная статистика.

**Ответ:**
```json
{
  "active_count": 120,
  "passive_count": 30,
  "unstable_count": 5,
  "zp_balance": {
    "effective": 12.50,
    "reserved": 1.00
  }
}
```
`zp_balance` — `null` если ZeroPoint ключ не задан.

---

### GET /api/faceunlock

Состояние Auto-Unlock-Face.

**Ответ:**
```json
{
  "zp_key": true,
  "auto_enabled": true,
  "interval_hours": 3.0,
  "next_run_at": "2026-05-24T15:30:00Z",
  "job": {
    "job_id": "abc123",
    "status": "processing",
    "total_accounts": 100,
    "processed": 60,
    "successful": 50,
    "failed": 8,
    "other_failed": 2,
    "result_files": ["unlocked.txt", "failed.txt"]
  }
}
```

- `zp_key` — bool, подключён ли ZP ключ
- `auto_enabled` — bool, включён ли авто-цикл
- `interval_hours` — float, один из пресетов: `1.0 / 2.0 / 3.0 / 4.0 / 6.0`
- `next_run_at` — ISO 8601 UTC, расчётное время следующего авто-запуска (`last_run_at + interval_hours`); `null` если авто-цикл выключен или ещё не запускался
- `job` — `null` если нет активной/завершённой задачи

---

### POST /api/faceunlock/start

Запустить задачу разблокировки. Берёт аккаунты с тегом `status:face` и отправляет в ZeroPoint.

**Тело запроса:** пустое `{}`

**Ответ (успех):**
```json
{
  "job_id": "abc123",
  "accounts": 47
}
```
- `accounts` — количество аккаунтов, отправленных в задачу

**Ответ (ошибка):**
```json
{
  "error": "Уже есть активная задача",
  "job_id": "abc123"
}
```
HTTP 409 если задача уже активна.

---

### POST /api/faceunlock/cancel

Отменить активную задачу.

**Тело:** `{}`

**Ответ:**
```json
{ "ok": true }
```

---

### PUT /api/faceunlock

Сохранить настройки авто-цикла.

**Тело запроса:**
```json
{
  "auto_enabled": true,
  "interval_hours": 3.0
}
```
Оба поля опциональны — передавать только то, что меняется.  
`interval_hours` — только из пресетов `[1.0, 2.0, 3.0, 4.0, 6.0]`.

**Ответ:**
```json
{
  "auto_enabled": true,
  "interval_hours": 3.0,
  "next_run_at": "2026-05-24T15:30:00Z"
}
```

---

### GET /api/faceunlock/download/{filename}

Скачать файл результата задачи.

**Параметры:** `filename` — имя файла из поля `result_files` в статусе задачи (напр. `unlocked.txt`)

**Ответ:** бинарный файл  
`Content-Type: application/octet-stream`  
`Content-Disposition: attachment; filename="{filename}"`

**Ошибки:**
- HTTP 404 — файл не найден или задача не существует
- HTTP 400 — нет активного job_id

---

### GET /api/autopilot

Полное состояние авто-пилота.

**Ответ (плоская структура — все поля на верхнем уровне):**
```json
{
  "running": true,
  "main_account": "username",
  "config_id": 5,
  "farm_config_id": 3,
  "check_interval": 30,
  "stuck_timeout": 10,
  "max_traders_per_server": 10,
  "started_at": "2026-05-24T10:00:00Z",
  "pets": [
    { "id": 1, "pet_id": "soggy_spring_2026_strawberry_shortcake_ducky" },
    { "id": 2, "pet_id": "basic_egg_2022_alicorn" }
  ],
  "queue": {
    "farming": 45,
    "trading": 3,
    "stuck": 1,
    "total": 49
  },
  "trades_done": 12,
  "recent": [
    { "time": "2026-05-24T10:31:00Z", "type": "trade_complete", "username": "player123" },
    { "time": "2026-05-24T10:29:00Z", "type": "got_pet",        "username": "player456" },
    { "time": "2026-05-24T10:25:00Z", "type": "stuck",          "username": "player789" },
    { "time": "2026-05-24T10:00:00Z", "type": "started",        "username": null }
  ]
}
```

> ⚠️ Нет обёртки `config:{}` — все поля конфига (`main_account`, `config_id`, `farm_config_id`, `check_interval`, `stuck_timeout`, `started_at`) возвращаются напрямую в корне объекта.

**Поля `queue`:**
- `farming` — аккаунты в статусе `farming` (фармят, пета нет)
- `trading` — аккаунты в статусе `trading` (пет есть, ждут трейда)
- `stuck` — аккаунты которые зависли (превышен `stuck_timeout`), в процессе возврата в `farming`
- `total` — сумма всех трёх

> ⚠️ Фронтенд НЕ должен читать `queue.active` / `queue.pending` / `queue.done` — эти поля не существуют.

**Поле `recent`** — последние 20 событий, новые первыми.  
Типы событий (`type`):

| Тип | Описание |
|---|---|
| `started` | Авто-пилот запущен |
| `stopped` | Авто-пилот остановлен |
| `got_pet` | Аккаунт получил нужного пета → переведён в trading |
| `trade_complete` | Аккаунт передал пета → возвращён в farming |
| `stuck` | Аккаунт завис (не передал пета за `stuck_timeout` мин) → возвращён в farming |

---

### POST /api/autopilot/start

Запустить авто-пилот.

**Тело:** `{}`

**Ответ (успех):**
```json
{ "queued": 148 }
```
- `queued` — количество аккаунтов добавленных в очередь (статус `farming`)

**Ошибки:**
- HTTP 400 `{"error": "Не задан main_account"}` — конфиг неполный
- HTTP 400 `{"error": "Не заданы pet_ids"}` — нет петов для отслеживания
- HTTP 409 `{"error": "Уже запущен"}` — пилот уже работает

---

### POST /api/autopilot/stop

Остановить авто-пилот. Отключает все активные аккаунты.

**Тело:** `{}`

**Ответ:**
```json
{ "ok": true }
```

---

### PUT /api/autopilot/config

Сохранить настройки авто-пилота. Все поля опциональны.

**Тело:**
```json
{
  "main_account": "username",
  "config_id": 5,
  "farm_config_id": 3,
  "check_interval": 30,
  "stuck_timeout": 10,
  "max_traders_per_server": 10
}
```

**Валидация:**
- `check_interval` — INTEGER, 10–300
- `stuck_timeout` — INTEGER, 1–60
- `max_traders_per_server` — INTEGER, 1–50

**Ответ:** обновлённый конфиг в том же плоском формате что и в GET /api/autopilot (поля `main_account`, `config_id`, `farm_config_id`, `check_interval`, `stuck_timeout`, `max_traders_per_server`)

---

### GET /api/autopilot/pets

Список петов авто-пилота.

**Ответ:**
```json
[
  { "id": 1, "pet_id": "soggy_spring_2026_strawberry_shortcake_ducky" },
  { "id": 2, "pet_id": "basic_egg_2022_alicorn" }
]
```

---

### POST /api/autopilot/pets

Добавить пет.

**Тело:**
```json
{ "pet_id": "soggy_spring_2026_strawberry_shortcake_ducky" }
```

**Ответ (успех):**
```json
{ "id": 3, "pet_id": "soggy_spring_2026_strawberry_shortcake_ducky" }
```

**Ответ (дубликат):** HTTP 409
```json
{ "error": "Такой пет уже добавлен" }
```

---

### DELETE /api/autopilot/pets/{id}

Удалить пет по `id` (row id из таблицы `autopilot_pets`).

**Ответ:** `{ "ok": true }`

---

### GET /api/autopilot/configs

Список игровых конфигов из AccountsOps (для выбора трейд/фарм конфига).

**Ответ:**
```json
[
  { "id": 1, "name": "Trade Config" },
  { "id": 2, "name": "Farm Config" }
]
```

---

### GET /api/alerts

**Ответ:**
```json
{
  "threshold": 50,
  "enabled": true,
  "triggered": false
}
```
`triggered` — `true` если порог уже сработал и ждём восстановления (информационное поле).

---

### PUT /api/alerts

**Тело (оба поля опциональны):**
```json
{
  "threshold": 50,
  "enabled": true
}
```

**Ответ:** обновлённый объект alerts (формат как GET /api/alerts).

---

## 8. Таблица событий авто-пилота (для `recent`)

Для ленты событий требуется отдельная таблица в БД. Бот пишет события в неё в процессе работы авто-пилота.

```sql
CREATE TABLE IF NOT EXISTS autopilot_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    username   TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

**event_type** — одно из: `started` / `stopped` / `got_pet` / `trade_complete` / `stuck`

**Когда писать событие:**

| Событие | Когда |
|---|---|
| `started` | При вызове ap_start, после построения очереди |
| `stopped` | При вызове ap_stop |
| `got_pet` | В цикле: аккаунт перешёл `farming → trading` |
| `trade_complete` | В цикле: аккаунт перешёл `trading → farming` (пет передан) |
| `stuck` | В цикле: аккаунт возвращён в farming по таймауту |

**Очистка:** при вызове `ap_start` удалять старые события этого user_id (или хранить последние N=100).

**Пример запроса в GET /api/autopilot:**
```sql
SELECT event_type, username, created_at
FROM autopilot_events
WHERE user_id = ?
ORDER BY id DESC
LIMIT 20
```

---

## 9. Что нужно поменять на фронтенде

### ❌ Критические (до интеграции)

**1. `queue` — названия полей**

Фронтенд читает `queue.active / queue.pending / queue.done` — таких полей нет.  
Правильные имена: `queue.farming / queue.trading / queue.stuck / queue.total`.

```js
// было
active: ap.queue?.active, pending: ap.queue?.pending, done: ap.queue?.done

// стало
farming: ap.queue?.farming, trading: ap.queue?.trading, stuck: ap.queue?.stuck, total: ap.queue?.total
```

---

**2. `GET /api/autopilot` — плоская структура (исправлено в этом спеке)**

Фронтенд читает `ap.main_account`, `ap.config_id` — это правильно.  
Спек обновлён: обёртки `config:{}` больше нет, все поля конфига идут напрямую в корне объекта.

---

**3. `recent` — формат события**

Фронтенд читает `r.status` (ищет `done / active`) и `r.elapsed_seconds`.  
Правильные поля: `r.type` и `r.time`.

```js
// было
r.status === 'done', r.elapsed_seconds

// стало
r.type === 'trade_complete', r.time  (ISO 8601 UTC)
```

Типы: `started / stopped / got_pet / trade_complete / stuck`

---

**4. `PUT /api/alerts` — тело запроса**

Фронтенд шлёт `{ toggle: true }` — сервер это не поймёт.  
Нужно слать `{ enabled: true }` или `{ enabled: false }`.

```js
// было
body: { toggle: true }

// стало
body: { enabled: !currentEnabled }
```

---

### ⚠️ Функциональные

**5. Configs endpoint — путь изменён**

```
было:  GET /api/player-configs
стало: GET /api/autopilot/configs
```

---

**6. `zp_balance` в dashboard**

`GET /api/dashboard` уже возвращает `zp_balance: { effective, reserved }` (или `null`).  
Нужно добавить в `mapDashboard`:

```js
zpBalance: data.zp_balance?.effective ?? null
```

---

### ➕ Новые поля для добавления в UI

**7. `farm_config_id`** — нужен второй `ModalConfigPicker` (аналогичный пикеру трейд-конфига).  
Вызывается кнопкой «🌾 Фарм конфиг», сохраняет через `PUT /api/autopilot/config { farm_config_id }`.

**8. `batch_size` — удалён**  
Поле `batch_size` удалено из бэкенда. Убрать степпер из UI и убрать из тела `PUT /api/autopilot/config`.

**9. `trades_done`** — уже возвращается в `GET /api/autopilot` как `trades_done: N`.  
Добавить в строку статуса, например: `Фармит: X · Торгует: Y · Сделок: Z`.

**10. `max_traders_per_server`** — степпер, диапазон 1–50, дефолт 10.  
Сохраняет через `PUT /api/autopilot/config { max_traders_per_server: N }`.  
Описание: «Макс. трейдеров на один VIP-сервер».
