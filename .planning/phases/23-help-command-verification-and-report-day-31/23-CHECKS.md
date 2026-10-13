# Phase 23: Recorded checks

Date: 2026-10-13; branch: Day31; commit: 357d09a (script as run in the final run); Python 3.13.15; Playwright 1.63.0 (Python package).

## VER-08 end-to-end run

Command: `python scripts/e2e_help_playwright.py` (from the repository root, isolated copy on ports 18000/18001, scratch database, real LM Studio, headless Chromium). Home-directory temp paths are shortened to `<tmp>`; the `HTTP Request:` log lines are dropped.

### Run 1 (2026-10-13, script commit 6170ac8, before the fix)

Exit code: 1 (the script's FAIL path; the shell wrapper of this run captured the output file only, not `$?`). Model id: qwen/qwen3.5-9b.

The single failed check was `git answer: tool card`. Cause: a defect in the script, not in the product. The script also required the full name `mcp__local-git__git_current_branch` in the card text, but the card summary shows `local-git · git_current_branch` (server and tool). The card itself was present, named the tool and showed `Branch: alpha`. The retry hit the same assertion. The script was fixed to match the card by server and tool names (as the Phase 22 script does); the full name is still asserted over HTTP in `tool_calls[].name`.

```
chat model: qwen/qwen3.5-9b
PASS: isolated copy patched to ports 18000/18001 - C:\Users\Aleksey\AppData\Local\Temp\help_e2e_um35wvok\app
PASS: isolated app instance is up (UI :18000 + Agent :18001)
PASS: folder upload: pending list - set_input_files; ['README.md', 'docs/ARCHITECTURE.md', 'docs/TESTING_GUIDE.md']
PASS: folder upload: base ready - e2e-project-docs | готово | 3 файла · 3 чанка | text-embedding-bge-m3 | Тест поиска | Добавить файлы | Удалить
PASS: git server added - C:\Users\Aleksey\AppData\Local\Temp\help_e2e_um35wvok\app\mcp_servers\project_git.py
PASS: base marked - e2e-project-docs
PASS: default tools - local-git checked=True; memory checked=False
PASS: badge shown - e2e-project-docs | Документация проекта | готово | 3 файла · 3 чанка | text-embedding-bge-m3 | Тест поиска | Добавить файлы | Удалить
PASS: usage notice - Справка по проектуИспользование: /help <вопрос> — ответ по документации проекта и состоянию git. Пример: /help как запустить приложение?Это 
--- failed attempt 1 transcript ---
Q: /help как запустить тесты проекта?
A: Тесты запускаются командой pytest tests/ -v для запуска всех тестов или pytest tests/test_name.py -v для запуска одного файла.
bubble: Справка по проекту · e2e-project-docsТесты запускаются командой pytest tests/ -v для запуска всех тестов или pytest tests/test_name.py -v для запуска одного файла.
44 tokensс RAG · K=5
        
      
sources: Источники (3)#1цитируется0.641docs/TESTING_GUIDE.mdTESTING_GUIDE3-0Загрузка фрагмента…показать полностью#20.488README.mdREADME1-0Загрузка фрагмента…показать полностью#30.453docs/ARCHITECTURE.mdARCHITE
Q: /help на какой ветке проект?
card: 🔧 local-git · git_current_branch — okАргументы{}РезультатBranch: alpha
A: Проект находится на ветке alpha.
failed: git answer: tool card
PASS: doc answer: label - Справка по проекту · e2e-project-docsДля запуска тестов проекта используйте команды pytest:
Запуск всех тестов: pytest 
PASS: doc answer: sources - Источники (3)#1цитируется0.641docs/TESTING_GUIDE.mdTESTING_GUIDE3-0Загрузка фрагмента…показать полностью#20.488README.md
PASS: doc answer: fact - Для запуска тестов проекта используйте команды pytest:
Запуск всех тестов: pytest tests/ -v [1]
Запуск одного файла тес
FAIL: git answer: tool card - 1 card(s); 🔧 local-git · git_current_branch — okАргументы{}РезультатBranch: alpha
PASS: git answer: branch - Проект находится на ветке alpha.
PASS: reload: two exchanges - ['/help как запустить тесты проекта?12 tokens\n        \n            \n            ↩ отсюда', '/help на какой ветке проект?10 tokens\n        \n            \n            ↩ отсюда']
PASS: reload: labels - ['Справка по проекту · e2e-project-docsДля запуска т', 'Справка по проекту · e2e-project-docsПроект находи']
PASS: reload: sources - 2 sources block(s)
PASS: reload: notice gone
PASS: no browser page errors
PASS: http: usage body - status=200; stored=False
PASS: http: doc answer - status=200; sources=3; kb_name=e2e-project-docs
PASS: http: git answer - status=200; tool_calls=['mcp__local-git__git_current_branch']
PASS: isolated ports are free again
scratch kept for inspection: C:\Users\Aleksey\AppData\Local\Temp\help_e2e_um35wvok
RESULT: FAIL (model qwen/qwen3.5-9b; failed: git answer: tool card)
```

Status: failed

### Run 2 (2026-10-13, after the script fix)

Exit code: 0. Model id: qwen/qwen3.5-9b. No retry was needed (attempt 1 of 2 passed in the browser and over HTTP).

```
chat model: qwen/qwen3.5-9b
PASS: isolated copy patched to ports 18000/18001 - C:\Users\Aleksey\AppData\Local\Temp\help_e2e_usahumcy\app
PASS: isolated app instance is up (UI :18000 + Agent :18001)
PASS: folder upload: pending list - set_input_files; ['README.md', 'docs/ARCHITECTURE.md', 'docs/TESTING_GUIDE.md']
PASS: folder upload: base ready - e2e-project-docs | готово | 3 файла · 3 чанка | text-embedding-bge-m3 | Тест поиска | Добавить файлы | Удалить
PASS: git server added - C:\Users\Aleksey\AppData\Local\Temp\help_e2e_usahumcy\app\mcp_servers\project_git.py
PASS: base marked - e2e-project-docs
PASS: default tools - local-git checked=True; memory checked=False
PASS: badge shown - e2e-project-docs | Документация проекта | готово | 3 файла · 3 чанка | text-embedding-bge-m3 | Тест поиска | Добавить файлы | Удалить
PASS: usage notice - Справка по проектуИспользование: /help <вопрос> — ответ по документации проекта и состоянию git. Пример: /help как запустить приложение?Это 
PASS: doc answer: label - Справка по проекту · e2e-project-docsДля запуска тестов проекта используйте команду pytest tests/ -v [1]. Если нужно про
PASS: doc answer: sources - Источники (3)#1цитируется0.641docs/TESTING_GUIDE.mdTESTING_GUIDE3-0Загрузка фрагмента…показать полностью#20.488README.md
PASS: doc answer: fact - Для запуска тестов проекта используйте команду pytest tests/ -v [1]. Если нужно протестировать один конкретный файл, зап
PASS: git answer: tool card - 1 card(s); 🔧 local-git · git_current_branch — okАргументы{}РезультатBranch: alpha
PASS: git answer: branch - Проект находится на ветке alpha.
PASS: reload: two exchanges - ['/help как запустить тесты проекта?12 tokens\n        \n            \n            ↩ отсюда', '/help на какой ветке проект?10 tokens\n        \n            \n            ↩ отсюда']
PASS: reload: labels - ['Справка по проекту · e2e-project-docsДля запуска т', 'Справка по проекту · e2e-project-docsПроект находи']
PASS: reload: sources - 2 sources block(s)
PASS: reload: notice gone
PASS: no browser page errors
PASS: http: usage body - status=200; stored=False
PASS: http: doc answer - status=200; sources=3; kb_name=e2e-project-docs
PASS: http: git answer - status=200; tool_calls=['mcp__local-git__git_current_branch']
PASS: isolated ports are free again
RESULT: PASS (model qwen/qwen3.5-9b)
```

Status: passed

## Notes for the report

Criterion 1: confirmed (a folder was uploaded through the real knowledge base form, reached `готово` with 3 files, and was marked in Settings; the card shows the badge `Документация проекта`).
Criterion 2: confirmed (`/help <question>` returned an answer labelled `Справка по проекту · e2e-project-docs` with a sources block listing `docs/TESTING_GUIDE.md` and the fact `pytest tests/ -v`, in the browser and over HTTP with `rag.sources` and `help.kb_name`).
Criterion 3: confirmed live; after reload only label, sources and quotes are restored
Criterion 4: confirmed (`/help` alone showed the temporary notice in the browser and returned the not-stored body over HTTP with `stored: false`; after the reload the notice is gone and only the two `/help <question>` exchanges remain).

Tool-call cards are live-only for every chat, not only for `/help`. They were asserted before the reload only; after the reload the label, the sources and the quotes are restored, and the cards are not.

## Test suite

Command: `python -m pytest tests/ -q -p no:cacheprovider` (2026-10-13, branch Day31, after the control run; run twice, the second run only to list skip reasons with `-rs`).

Summary line as printed: `3150 passed, 13 skipped, 32 warnings in 577.31s (0:09:37)`. Exit code 0; no failed tests. (The earlier Plan 23-05 run had one timing failure, `tests/test_kb_api.py::test_create_happy_path`; it did not recur.)

Skipped tests (13) are skipped for an environment reason and are not passed checks:
- `tests/test_kb_real_pdfs.py` (10 tests): real PDF files are not available.
- `tests/test_live_deepseek_title.py` (1): live paid DeepSeek call; needs `RUN_LIVE_DEEPSEEK=1`.
- `tests/test_mcp_real_github.py` (2): `github-mcp-server.exe` or `GITHUB_PERSONAL_ACCESS_TOKEN` is missing.

Status: passed
