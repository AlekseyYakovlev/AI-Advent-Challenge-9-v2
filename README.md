# AiAdventAgentV2

AiAdventAgentV2 is a local-first AI chat application made of two Python processes: a UI server on port 8000 and an Agent server on port 8001. The frontend is vanilla JavaScript served as static files, data is stored in SQLite, and answers come from DeepSeek (cloud) or LM Studio (local) as LLM backends. It runs on one machine without Docker or any message broker.

## What it does

- Tree-shaped chat history: every message has a parent, so a conversation can be branched, edited and regenerated without copying data.
- Context compression strategies that decide what is sent to the model: `sliding`, `sticky`, `truncate_middle` and `no_compression`. The database always keeps the full history.
- User accounts with username and password login and an HTTP-only session cookie.
- Long-term memory and a user profile that personalise the agent.
- Tasks with a lifecycle and invariants (rules the agent must respect).
- A scheduler that runs jobs in the background.
- MCP servers: external tool servers started as subprocesses and used by the model during a chat turn.
- Knowledge bases with RAG answers and source citations.
- API keys and an HTTP chat endpoint for calling the agent from scripts.
- LAN mode: the app can be made reachable from the local network over plain HTTP.
- The `/support` command: a support assistant that answers from the project docs (including `docs/FAQ.md`) and from ticket and user data read through the support MCP server.
- Automated review of GitHub Pull Requests: a workflow asks the app for a review that uses the project docs and code.
- A project files assistant: the bundled `project-files` MCP entry (`mcp_servers/project_git.py --allow-write`) lets the model read, search and edit files of a repository clone from an ordinary chat, with a coloured diff card per change. The scripts `scripts/files_task.py` and `scripts/files_eval.py` run goals and the report scenarios from the command line. See [docs/USER_GUIDE.md](docs/USER_GUIDE.md) and `Day34_report.md`.
- The `/release` command: prepares a release from git history - a Russian changelog section, a version and title, then (after confirmation) commit, annotated tag, push and a draft GitHub Release through the `release-git` MCP entry (`mcp_servers/project_git.py --allow-release`). See the section «Команда /release» of [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

## Directory structure

- `agent/` - the Agent process: REST API, WebSocket chat, LLM clients, context engine, memory, tasks, scheduler, MCP client and the RAG pipeline.
- `mcp_servers/` - bundled MCP servers started as subprocesses: `project_git.py` and the support desk server `support_desk.py` with its sample data `support_data.json`.
- `ui/` - the UI process: serves the static frontend (`ui/static/`) and supervises the Agent subprocess.
- `shared/` - code used by both processes: configuration, database, models, logging.
- `scripts/` - command-line helpers: browser end-to-end scripts (`e2e_*_playwright.py`), RAG evaluation and dialog runners, MCP tool listing.
- `tests/` - the pytest test suite.
- `docs/` - project documentation (user guide, API specification, architecture, testing guide).
- `run.py` - the entry point that starts the application.
- `requirements.txt` - Python dependencies.
- `pytest.ini` - pytest configuration.
- `.env.example` - template of the supported settings.

The files and folders `app.db`, `app_kb/`, `logs/` and `eval_out/` are created at run time and are not source.

## Requirements

- Python 3.11 or newer.
- An LLM backend: a DeepSeek API key, or LM Studio running at `http://localhost:1234`.
- Knowledge bases need LM Studio with an embedding model loaded.
- No Docker is needed.

## Install

Install the dependencies:

```bash
pip install -r requirements.txt
```

Then copy `.env.example` to `.env` and edit the values you need.

## Run

Start the application:

```bash
python run.py
```

Open the UI at `http://localhost:8000`. The UI process starts the Agent process on port 8001, checks its health and restarts it if it stops.

On the first run with an empty database the app creates a bootstrap account with the username `admin` and a generated password. The password is printed once in the terminal where `python run.py` runs and is not shown again, so save it.

## Configuration (.env)

Settings are read from the `.env` file. Restart `python run.py` after changing them.

| Setting | Default | Meaning |
|---------|---------|---------|
| `UI_PORT` | 8000 | Port of the UI server. |
| `AGENT_PORT` | 8001 | Port of the Agent server. |
| `BIND_HOST` | 127.0.0.1 | Bind address for both servers: `127.0.0.1` for this computer only, or `0.0.0.0` for the whole local network. |
| `DB_PATH` | app.db | Path of the SQLite database file. |
| `DEEPSEEK_API_KEY` | empty | API key for the DeepSeek cloud backend. |
| `LM_STUDIO_BASE_URL` | http://localhost:1234 | Address of the local LM Studio server. |
| `LLM_TIMEOUT` | 60.0 | Timeout in seconds for LLM requests. |
| `RATE_LIMIT_MESSAGES_PER_MINUTE` | 30 | Messages per minute per chat; 0 turns the limit off. |
| `LOGIN_MAX_FAILED_ATTEMPTS` | 5 | Wrong passwords before login is blocked; 0 turns the limit off. |
| `LOGIN_LOCKOUT_SECONDS` | 300 | Duration of a login block in seconds. |
| `MAX_CONCURRENT_GENERATIONS` | 2 | Chat turns generating at once; 0 means no cap. |
| `MCP_AUTO_CONNECT` | true | Connect enabled MCP servers automatically at the first chat turn. |
| `MCP_TOOL_CALL_TIMEOUT` | 30.0 | Timeout in seconds for one MCP tool call. |
| `KB_STORAGE_DIR` | empty | Knowledge-base storage folder; empty means a folder named after the database file with the suffix `_kb` next to it. |

## Automated PR review

A GitHub Action (`.github/workflows/pr-review.yml`) runs on a self-hosted runner on the owner's machine. The script `scripts/pr_review_ci.py` asks the Agent (`POST /api/v1/reviews`) for a review of the Pull Request and publishes it as one comment with three sections: potential bugs, architecture problems and recommendations. The Agent reads the Pull Request through the GitHub MCP server, searches the project documentation and an index of the base branch code, and asks a local or cloud model. The check is advisory and never blocks a merge.

Because the repository is public, read the safe runner configuration before registering a runner: see the section on automated PR review in [docs/USER_GUIDE.md](docs/USER_GUIDE.md). The route reference is in [docs/API_SPEC.md](docs/API_SPEC.md), and the design is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The Day 32 results are described in `Day32_report.md`.

## Knowledge base from the project docs

The README and the `docs/` folder can be loaded into one knowledge base from the Knowledge base panel of the app: choose `README.md`, then choose the `docs` folder, and create the base. Sources keep their relative paths, for example `docs/API_SPEC.md`, so same-named files in different folders stay separate. When a document changes, choose the folder again with the add-files button of the ready base: changed files replace their earlier fragments and unchanged ones are skipped. See `docs/USER_GUIDE.md` for the steps.

## Tests

Run the whole suite:

```bash
pytest tests/ -v
```

Tests use a separate database (`test_app.db`), and the browser scripts in `scripts/e2e_*_playwright.py` run on an isolated copy of the app at ports 18000 and 18001.

## Documentation

- [docs/USER_GUIDE.md](docs/USER_GUIDE.md) - how to use the chat, settings and statistics.
- [docs/API_SPEC.md](docs/API_SPEC.md) - the REST and WebSocket API.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - the process model, data model and context strategies.
- [docs/FAQ.md](docs/FAQ.md) - frequently asked questions and fixes, also used as documentation for `/support`.
- [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) - the test scenarios each test file must cover.
