# Nuclear CLI AI

Голосовой ассистент для [Nuclear](https://github.com/nukeop/nuclear) — аналог
«Яндекс станции» на своём ПК. Текущий этап: **1 — текстовые команды в консоли**.

```
команда → regex-роутер (мгновенно) → LLM (Ollama, tool calling) → Nuclear MCP → плеер
```

## Требования

- Nuclear с включённым MCP (`Settings → Integrations → Enable MCP Server`)
  и плагином puer (metadata-провайдер выбран в Sources);
- [Ollama](https://ollama.com/download) + модель: `ollama pull qwen3:4b`;
- Python 3.10+.

## Запуск (Windows, PowerShell)

```powershell
cd nuclear-cli-ai
pip install -r requirements.txt
python assistant.py
```

Примеры команд:

| Команда | Что происходит |
| --- | --- |
| `включи нирвану` | LLM → `play_artist` → топ-треки исполнителя в очередь |
| `поставь smells like teen spirit` | LLM → `play_track` → первый найденный трек |
| `включи альбом nevermind` | LLM → `play_album` → альбом целиком |
| `плейлист rock classics` | LLM → `play_playlist` → сначала свои плейлисты, потом YT Music |
| `дальше`, `пауза`, `громче`, `что играет`, `в избранное` | без LLM, мгновенно |

## Конфиг (переменные окружения)

| Переменная | По умолчанию | Зачем |
| --- | --- | --- |
| `NUCLEAR_MCP_URL` | `http://127.0.0.1:8800/mcp` | адрес из Settings → Integrations |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | можно указать другой хост (напр. с Raspberry Pi на ПК) |
| `OLLAMA_MODEL` | `qwen3:4b` | на Pi: `qwen3:1.7b` или `qwen3:0.6b` |
| `OLLAMA_KEEP_ALIVE` | `30m` | сколько держать модель в памяти |

```powershell
$env:OLLAMA_MODEL = "qwen3:1.7b"; python assistant.py
```

## Архитектура и решения

- **Роутер до LLM**: частые команды (пауза/дальше/громче/лайк) обрабатываются
  regex'ами без модели — ноль латентности; на Raspberry Pi это большинство команд.
- **Узкие инструменты вместо сырого MCP**: модель выбирает из 11 понятных действий
  (`play_track`, `play_artist`, ...), а не исследует API Nuclear через
  `list_methods`/`describe_type` (это экономит 3-4 раунда на команду).
- **Один вызов LLM на команду**: результат инструмента озвучивается напрямую,
  без второго круга через модель.
- `think: false` для qwen3 — рассуждения дают +2-5 секунд латентности.
- Шкала громкости Nuclear не задокументирована — определяется по текущему значению.

## Дальше по плану

- Этап 2: голос — `faster-whisper` (STT), `piper` (TTS, русские голоса), `openWakeWord`.
- Этап 3: демон с wake word; вариант переноса на Raspberry Pi
  (лёгкая модель `qwen3:1.7b`/`0.6b` или Pi как сателлит с LLM на ПК).
