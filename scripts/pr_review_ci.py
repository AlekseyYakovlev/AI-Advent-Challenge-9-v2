"""CI caller of the PR review route: asks the local app for a review and publishes it in the Pull Request.

Configuration comes from environment variables only (nothing secret is taken
from the command line):

    APP_URL             base URL of the app (default http://127.0.0.1:8001)
    API_KEY             API key of the app, sent only in the Authorization header
    GITHUB_TOKEN        token of the workflow, used to write the PR comment
    REPO                owner/name of the repository
    PR_NUMBER           Pull Request number
    HEAD_SHA            head commit of the event (optional)
    GITHUB_STEP_SUMMARY file that receives the job summary (set by Actions)
    GITHUB_API_URL      GitHub REST API root (default https://api.github.com)

The file uses only the Python standard library and imports nothing from the
project. Exit code 0 means the review was published, 1 means it was not.
"""

import dataclasses
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any

if sys.version_info < (3, 11):
    sys.exit("Python 3.11 or newer is required")

MARKER = "<!-- ai-pr-review -->"
BOT_LOGIN = "github-actions[bot]"
COMMENT_MAX_CHARS = 60000
REVIEW_TIMEOUT_SECONDS = 3300.0
GITHUB_TIMEOUT_SECONDS = 30.0

DEFAULT_APP_URL = "http://127.0.0.1:8001"
DEFAULT_GITHUB_API = "https://api.github.com"
EMPTY_SECTION = "Замечаний нет."
NO_ITEMS = "нет"
SOURCES_CUT_LINE = "Список источников сокращён."
TEXT_CUT_LINE = "Текст ревью сокращён до допустимого размера комментария."
DETAIL_MAX_CHARS = 300
PAGE_SIZE = 100
ZERO_WIDTH_SPACE = "​"

SECTION_TITLES = (
    ("bugs", "Потенциальные баги"),
    ("architecture", "Архитектурные проблемы"),
    ("recommendations", "Рекомендации"),
)

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_MENTION_RE = re.compile(r"@(?=[A-Za-z0-9])")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_SECRETS: list[str] = []

GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


@dataclasses.dataclass
class Reply:
    """Outcome of one HTTP call."""

    status: int | None
    body: Any
    error: str | None = None


def scrub(text: str) -> str:
    """Remove every known secret from a piece of text."""
    for secret in _SECRETS:
        if secret:
            text = text.replace(secret, "[секрет]")
    return text


def say(message: str) -> None:
    """Print one console line without secrets."""
    print(scrub(message), flush=True)


def call(method: str, url: str, key: str | None, body: Any, timeout: float,
         extra_headers: dict[str, str] | None = None) -> Reply:
    """Perform one HTTP request without proxies and return a Reply."""
    headers = {"Accept": "application/json"}
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            status: int | None = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read()
        except (urllib.error.URLError, OSError, http.client.HTTPException) as inner:
            return Reply(status, None, f"{type(inner).__name__}: {inner}")
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        return Reply(None, None, f"{type(exc).__name__}: {exc}")
    parsed: Any = None
    if raw:
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
    return Reply(status, parsed)


def defuse(text: str) -> str:
    """Neutralise user mentions and remove Markdown images from model text."""
    text = _IMAGE_RE.sub("", text)
    return _MENTION_RE.sub("@" + ZERO_WIDTH_SPACE, text)


def _section_text(value: Any) -> str:
    """Return a section body, or the empty-section line when nothing is there."""
    text = defuse(str(value or "")).strip()
    return text or EMPTY_SECTION


def _doc_item(source: dict[str, Any]) -> str:
    """Render one documentation source."""
    file = str(source.get("file", ""))
    section = source.get("section")
    return f"`{file}` — {section}" if section else f"`{file}`"


def _code_item(source: dict[str, Any]) -> str:
    """Render one code source as path or path:start-end."""
    file = str(source.get("file", ""))
    lines = source.get("lines")
    return f"`{file}:{lines}`" if lines else f"`{file}`"


def _list_lines(items: list[str]) -> list[str]:
    """Render a bullet list, or the no-items line."""
    return [f"- {item}" for item in items] or [NO_ITEMS]


def _sources_block(docs: list[str], code: list[str], cut: bool) -> list[str]:
    """Render the collapsed sources block."""
    lines = ["<details><summary>Источники</summary>", "", "**Документация**", ""]
    lines += _list_lines(docs) + ["", "**Код**", ""] + _list_lines(code)
    if cut:
        lines += ["", SOURCES_CUT_LINE]
    return lines + ["", "</details>"]


def _header_lines(review: dict[str, Any]) -> list[str]:
    """Render marker, title, commit, model and informational lines."""
    sha = str(review.get("head_sha", ""))[:7]
    model = f"{review.get('provider', '')}/{review.get('model', '')}"
    lines = [MARKER, "## Ревью Pull Request", "", f"Коммит: `{sha}`", f"Модель: `{model}`"]
    extra = [defuse(str(note)) for note in review.get("notes") or []]
    unreviewed = review.get("unreviewed_files") or []
    if unreviewed:
        extra.append("Не вошли в ревью файлы: " + ", ".join(f"`{f}`" for f in unreviewed))
    if not review.get("docs_used", True):
        extra.append("Документация не использовалась.")
    return lines + ([""] + extra if extra else [])


def _render(review: dict[str, Any], sections: dict[str, str], docs: list[str],
            code: list[str], sources_cut: bool, text_cut: bool) -> str:
    """Assemble the full comment text from prepared parts."""
    lines = _header_lines(review)
    if text_cut:
        lines += ["", TEXT_CUT_LINE]
    for key, title in SECTION_TITLES:
        lines += ["", f"### {title}", "", sections[key]]
    lines += [""] + _sources_block(docs, code, sources_cut)
    return "\n".join(lines) + "\n"


def truncate_comment(review: dict[str, Any], sections: dict[str, str],
                     docs: list[str], code: list[str]) -> str:
    """Fit the comment in the size limit: cut sources first, then the sections."""
    text = _render(review, sections, docs, code, False, False)
    if len(text) <= COMMENT_MAX_CHARS:
        return text
    keep = max(len(docs), len(code))
    while keep > 0:
        keep //= 2
        text = _render(review, sections, docs[:keep], code[:keep], True, False)
        if len(text) <= COMMENT_MAX_CHARS:
            return text
    overflow = len(text) - COMMENT_MAX_CHARS + 200
    total = sum(len(value) for value in sections.values())
    ratio = max(0.0, 1 - overflow / max(total, 1))
    cut = {k: v[:int(len(v) * ratio)].rstrip() + " …" for k, v in sections.items()}
    text = _render(review, cut, [], [], True, True)
    while len(text) > COMMENT_MAX_CHARS and ratio > 0:
        ratio *= 0.9
        cut = {k: v[:int(len(v) * ratio)].rstrip() + " …" for k, v in sections.items()}
        text = _render(review, cut, [], [], True, True)
    return text


def build_comment(review: dict[str, Any]) -> str:
    """Build the Markdown comment of a review reply."""
    raw = review.get("sections") or {}
    sections = {key: _section_text(raw.get(key)) for key, _ in SECTION_TITLES}
    sources = review.get("sources") or {}
    docs = [_doc_item(s) for s in sources.get("docs") or []]
    code = [_code_item(s) for s in sources.get("code") or []]
    return scrub(truncate_comment(review, sections, docs, code))


def failure_line(url: str, reply: Reply) -> str:
    """Map a failed review call to one Russian summary line."""
    if reply.status is None:
        return (f"Ревью не выполнено: приложение недоступно по адресу {url}. "
                "Проверьте, что приложение запущено и адрес указан верно.")
    if reply.status in (401, 403):
        return ("Ревью не выполнено: ключ API отклонён. Проверьте секрет репозитория "
                "с ключом API и совпадение с ключом в приложении.")
    body = reply.body if isinstance(reply.body, dict) else None
    if body is None:
        return f"Ревью не выполнено: приложение ответило HTTP {reply.status}."
    code = str(body.get("code", ""))
    detail = str(body.get("detail", ""))[:DETAIL_MAX_CHARS]
    if code == "GITHUB_MCP_UNAVAILABLE":
        return ("Ревью не выполнено: сервер GitHub не настроен или не подключён. "
                "Подключите сервер `github-mcp-server` в настройках «MCP серверы» и повторите.")
    if code in ("LM_STUDIO_UNAVAILABLE", "EMBEDDER_UNAVAILABLE"):
        return ("Ревью не выполнено: LM Studio недоступен. Запустите LM Studio, "
                "загрузите модель и перезапустите задачу.")
    if code in ("LLM_UNREACHABLE", "LLM_ERROR", "REVIEW_BAD_OUTPUT"):
        return (f"Ревью не выполнено: модель вернула ошибку: {detail}. Проверьте провайдера "
                "и модель в настройках «Ревью Pull Request» и перезапустите задачу.")
    return f"Ревью не выполнено: {detail} (код {code})."


def find_own_comment(comments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the newest comment carrying the marker and written by the bot."""
    own = [
        c for c in comments
        if str(c.get("body", "")).startswith(MARKER)
        and (c.get("user") or {}).get("login") == BOT_LOGIN
    ]
    return max(own, key=lambda c: c.get("id", 0)) if own else None


def list_comments(api: str, repo: str, number: int, token: str) -> tuple[list[dict[str, Any]], Reply]:
    """Fetch all comments of the PR, following pages; return them and the last reply."""
    comments: list[dict[str, Any]] = []
    page = 1
    while True:
        url = f"{api}/repos/{repo}/issues/{number}/comments?per_page={PAGE_SIZE}&page={page}"
        reply = call("GET", url, token, None, GITHUB_TIMEOUT_SECONDS, GITHUB_HEADERS)
        if reply.status != 200 or not isinstance(reply.body, list):
            return comments, reply
        comments.extend(c for c in reply.body if isinstance(c, dict))
        if len(reply.body) < PAGE_SIZE:
            return comments, reply
        page += 1


def publish(api: str, repo: str, number: int, token: str, text: str) -> Reply:
    """Replace the job's own earlier comment or create a new one."""
    comments, last = list_comments(api, repo, number, token)
    if last.status != 200:
        return last
    own = find_own_comment(comments)
    payload = {"body": text}
    if own is not None:
        url = f"{api}/repos/{repo}/issues/comments/{own['id']}"
        return call("PATCH", url, token, payload, GITHUB_TIMEOUT_SECONDS, GITHUB_HEADERS)
    url = f"{api}/repos/{repo}/issues/{number}/comments"
    return call("POST", url, token, payload, GITHUB_TIMEOUT_SECONDS, GITHUB_HEADERS)


def publish_failure_line(reply: Reply) -> str:
    """Map a failed comment call to one Russian summary line."""
    if reply.status is None:
        return "Комментарий не опубликован: GitHub недоступен."
    return (f"Комментарий не опубликован: GitHub ответил HTTP {reply.status}. "
            "Проверьте права токена задачи (pull-requests: write; "
            "при отказе добавьте issues: write).")


def write_summary(text: str) -> None:
    """Append text to the job summary file, or print it when run locally."""
    path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    clean = scrub(text)
    if not path:
        say(clean)
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(clean + "\n\n")


def fail(message: str) -> int:
    """Report a failure to the console and the summary and return the exit code."""
    say(message)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        write_summary(message)
    return 1


def read_config() -> tuple[dict[str, str] | None, str]:
    """Read and validate the environment; return the config or an error line."""
    env = os.environ
    for name in ("API_KEY", "GITHUB_TOKEN", "REPO", "PR_NUMBER"):
        if not env.get(name, "").strip():
            return None, f"Ревью не выполнено: не задана переменная {name}."
    repo = env["REPO"].strip()
    number = env["PR_NUMBER"].strip()
    if not _REPO_RE.match(repo):
        return None, "Ревью не выполнено: переменная REPO имеет неверный формат."
    if not number.isdigit() or int(number) <= 0:
        return None, "Ревью не выполнено: переменная PR_NUMBER имеет неверный формат."
    sha = env.get("HEAD_SHA", "").strip()
    config = {
        "url": (env.get("APP_URL", "").strip() or DEFAULT_APP_URL).rstrip("/"),
        "key": env["API_KEY"].strip(),
        "token": env["GITHUB_TOKEN"].strip(),
        "repo": repo,
        "number": number,
        "sha": sha if _SHA_RE.match(sha) else "",
        "api": (env.get("GITHUB_API_URL", "").strip() or DEFAULT_GITHUB_API).rstrip("/"),
    }
    return config, ""


def main(argv: list[str] | None = None) -> int:
    """Request a review, publish it as a PR comment and report the result."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    _SECRETS[:] = [os.environ.get("API_KEY", "").strip(), os.environ.get("GITHUB_TOKEN", "").strip()]
    config, error = read_config()
    if config is None:
        return fail(error)
    body = {"repo": config["repo"], "pr_number": int(config["number"]), "head_sha": config["sha"]}
    started = time.monotonic()
    reply = call("POST", f"{config['url']}/api/v1/reviews", config["key"], body, REVIEW_TIMEOUT_SECONDS)
    if reply.status != 200 or not isinstance(reply.body, dict):
        return fail(failure_line(config["url"], reply))
    say(f"Ревью получено за {time.monotonic() - started:.0f} с.")
    text = build_comment(reply.body)
    write_summary(text)
    posted = publish(config["api"], config["repo"], int(config["number"]), config["token"], text)
    if posted.status not in (200, 201):
        return fail(publish_failure_line(posted))
    say("Комментарий опубликован.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
