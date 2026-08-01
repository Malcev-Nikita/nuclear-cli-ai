# CLAUDE.md

Контекст для Claude Code. Проверенные факты, не выводы.

## Что это

Голосовой ассистент для Nuclear («Яндекс станция» на ПК пользователя).
Цепочка: команда → regex-роутер → (если не распознано) LLM через Ollama с узкими
инструментами → MCP-сервер Nuclear → плеер.

Этап 1 (`assistant.py`, текстовые команды) **работает вживую у пользователя**
(подтверждено 2026-08-01). Текущий этап — **2: голос** (`voice.py`, собран
2026-08-01, ждёт живого прогона; TTS ещё не делали).

Связанный проект: `/home/claude/projects/nuclear-plugin-puer` — плагин для Nuclear
(`puer-ytmusic` metadata / `puer-import` playlists / `puer-youtube` streaming).
Без него `Metadata.search` в Nuclear пуст — ассистенту нечего искать. Детали
Nuclear/InnerTube/MCP — в CLAUDE.md того проекта; здесь только то, что нужно агенту.

## Окружение пользователя

- Nuclear + Ollama на Windows-ПК пользователя; из песочницы Claude Code до них НЕ
  достучаться. Живые проверки — командами, которые пользователь запускает в
  PowerShell (не bash; `curl` там алиас Invoke-WebRequest, тело слать байтами UTF-8).
- У пользователя: Python 3.14, GPU (~170 ток/с на qwen3:1.7b), модели qwen3 4b/1.7b.
- Дальняя цель — перенос на Raspberry Pi: либо лёгкая модель (qwen3:1.7b/0.6b),
  либо Pi как голосовой сателлит, а LLM остаётся на ПК (Nuclear всё равно на ПК).

## Запуск и проверка

```bash
# у пользователя (Windows):
python -m pip install -r requirements.txt && python assistant.py   # этап 1, текст
python -m pip install -r requirements-voice.txt && python voice.py # этап 2, голос

# в песочнице (python нет, есть uv):
uv run --python 3.12 --with requests python assistant.py
```

Конфиг через env: `NUCLEAR_MCP_URL` (деф. `http://127.0.0.1:8800/mcp`),
`OLLAMA_URL` (`http://127.0.0.1:11434`), `OLLAMA_MODEL` (`qwen3:4b`), `OLLAMA_KEEP_ALIVE` (`30m`).

## Архитектурные решения (не менять без причины)

- **Роутер до LLM**: частые команды (пауза/дальше/громче/лайк/что играет) — regex,
  ноль латентности. На Pi это большинство команд, и это главный способ сделать Pi реальным.
- **11 узких инструментов вместо сырых 4 мета-инструментов Nuclear MCP**
  (`list_methods`/`call`...): иначе модель тратит 3-4 discovery-раунда на команду.
- **Один вызов LLM на команду**: результат инструмента отдаётся пользователю напрямую,
  без второго круга через модель (латентность).
- `think: false` для qwen3 — иначе +2-5 сек рассуждений; при 400 с упоминанием think
  агент сам ретраит без параметра (старые Ollama).
- «Включи X» **заменяет** очередь (clearQueue → addToQueue → goToIndex(0) → play),
  как у Яндекс станции.
- **Wake word без openWakeWord** (`voice.py`): под русское имя «Игорь» готовой
  модели нет, тренировать свою — отдельный проект. Вместо этого whisper
  распознаёт каждую фразу (энергетический VAD режет речь на фразы), а ассистент
  реагирует только если в первых 3 словах есть имя из `ASSISTANT_NAMES`
  (нечётко, Левенштейн ≤1 — ловит «Егорь»/«Игор», побочно и падежи «Игоря»).
  Имя без команды → бип + окно 8 сек, когда команда принимается без имени.
  Известная цена: whisper молотит всё подряд, включая музыку из колонок —
  на ПК с GPU ок, для Pi решение пересмотрим (см. этап 3).
- Плейлисты: сначала локальные Nuclear (Playlists.getIndex, вхождение имени),
  потом поиск YT Music → `Metadata.fetchAlbumDetails` (принимает id плейлистов VL…/PL…).

## Протокольные грабли (каждая стоила отладки)

- **MCP handshake**: `Accept: application/json, text/event-stream` — оба типа
  обязательны (иначе 406). Session id — из заголовка `mcp-session-id` ответа на
  initialize; далее слать `Mcp-Session-Id` на каждом запросе; `notifications/initialized` → 202.
- **Ответы Nuclear — SSE-обёртка**: строки `data: {...}` (первая data бывает пустой),
  полезный результат — JSON-строкой в `result.content[0].text`.
- **Кириллица**: `text/event-stream` приходит без charset → requests декодирует
  latin-1 → каша. Декодировать `resp.content` как UTF-8 самим (уже в `_parse_rpc`).
- **`Metadata.search`**: параметр метода буквально называется `params` →
  `{"params": {"params": {"query": ...}}}` — вложенность не опечатка.
- Шкала громкости Nuclear не задокументирована (0-1 или 0-100) — агент определяет
  по текущему значению `Playback.getVolume`.
- PowerShell `Invoke-WebRequest` кодирует строковое тело НЕ в UTF-8 — в примерах
  для пользователя слать `[System.Text.Encoding]::UTF8.GetBytes(...)`.
- **faster-whisper на GPU (Windows)**: `WhisperModel(device="cuda")` создаётся
  успешно, а падает только первый encode — «Library cublas64_12.dll is not
  found». Лечение: pip-пакеты `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` (уже в
  requirements-voice.txt) + `os.add_dll_directory` на их bin (есть в voice.py,
  `_add_cuda_dll_dirs`). Поэтому же в Transcriber есть warm-up при старте —
  чтобы фолбэк на cpu срабатывал сразу, а не на первой фразе.

## Тестовый стенд (без Nuclear и Ollama)

Два фейка на Node (жили в scratchpad как `fake-nuclear.mjs` / `fake-ollama.mjs`,
при необходимости воссоздать):

- **fake-nuclear**: `@modelcontextprotocol/sdk` StreamableHTTPServerTransport на
  127.0.0.1:8800/mcp, один инструмент `call`, внутри switch по `Domain.method` с
  каноническими ответами (форматы сняты с реального Nuclear пользователя); результат
  заворачивать `JSON.stringify(...)` в `content[0].text`. Важно: новый McpServer на
  каждую сессию (повторный connect одного инстанса падает).
- **fake-ollama**: express, `GET /api/version` + `POST /api/chat`, маршрутизация по
  ключевым словам → `tool_calls` как у qwen3.

Прогон: `printf 'команды\nq\n' | uv run --python 3.12 --with requests python assistant.py`.
Эталонный набор — 15 сценариев (артист/трек/альбом/плейлист локальный и YT/все
команды роутера/не-музыкальный вопрос).

## План

- Этап 1 ✅ работает вживую (2026-08-01).
- Этап 2: голос. STT ✅ собран (`voice.py`: sounddevice → энергетический VAD →
  faster-whisper → wake-имя → agent.handle). Ждёт живого прогона; риски:
  wheels ctranslate2/sounddevice под Python 3.14 (если pip не соберёт —
  ставить через `py -3.12`), ложные срабатывания VAD на музыке из колонок,
  качество распознавания на `small`. Дальше в этапе 2 — TTS `piper`.
- Этап 3: демон с автозапуском; вариант Pi-сателлита. Для Pi текущий
  wake-подход (whisper на всё подряд) дорог — вернуться к openWakeWord
  (тренировка кастомного имени) или whisper tiny только как детектор имени.
- Push-событий смены трека в MCP нет; если понадобятся — SSE `/api/events`
  HTTP API Nuclear (`integrations.jam`).
