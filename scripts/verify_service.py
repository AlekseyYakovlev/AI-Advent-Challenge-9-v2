"""Verify a running AiAdventAgent service from another machine.

Checks reachability and authentication, sends N parallel requests to a real
model and reports timings and queue waits, and demonstrates the per-chat
message rate limit and the context-overflow answer.

Usage:
    python3 verify_service.py --url http://<lan-ip>:8001 --key aia_...

The file uses only the Python standard library (3.11 or newer) and imports
nothing from the project, so it can be copied alone to a macOS or Windows
machine and run as is. The key may also come from the AIA_API_KEY
environment variable. The key is never written to the output files or the
console; only its first 8 characters are recorded.

Results go to the console and to two files in the output directory:
verify_results.md (Markdown tables) and verify_results.json (raw records).

Exit codes:
    0  every check and demonstration behaved as expected
    1  a check ran and an expectation was not met
    2  preflight failure (bad arguments, server unreachable, key refused,
       no usable model)
"""

import argparse
import concurrent.futures
import dataclasses
import hashlib
import http.client
import json
import os
import pathlib
import platform
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

INVALID_KEY = "aia_invalid_key_for_verification_0000000000000"
NOT_RUN = "не запускалось"
RATE_MODES = ("chat", "fast", "both")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_SECRETS: list[str] = []

PARALLEL_PROMPT = "Назови одну столицу европейской страны и ответь одним коротким предложением."
RATE_PROMPT = "Ответь одним словом: да."
RATE_SYSTEM_PROMPT = "Отвечай строго одним словом, без пояснений."
_WORDS = (
    "река лес камень ветер огонь облако дорога мост город море гора поле "
    "птица звезда ночь утро зима осень весна лето песок трава дом окно книга"
).split()


@dataclasses.dataclass
class Reply:
    """Outcome of one HTTP call."""

    status: int | None
    headers: dict[str, str]
    body: Any
    elapsed: float
    error: str | None = None


def scrub(text: str) -> str:
    """Remove every known secret from a piece of text."""
    for secret in _SECRETS:
        if secret:
            text = text.replace(secret, "[key]")
    return text


def say(message: str) -> None:
    """Print one console line without secrets."""
    print(scrub(message), flush=True)


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


def header_value(reply: Reply, name: str) -> str | None:
    """Look up a response header case-insensitively."""
    wanted = name.lower()
    for key, value in reply.headers.items():
        if key.lower() == wanted:
            return value
    return None


def call(method: str, url: str, key: str | None, body: Any, timeout: float) -> Reply:
    """Perform one HTTP request without proxies and return a Reply."""
    headers = {"Accept": "application/json"}
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    start = time.perf_counter()
    status: int | None
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            status = response.status
            resp_headers = dict(response.headers.items())
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        resp_headers = dict(exc.headers.items()) if exc.headers else {}
        try:
            raw = exc.read()
        except (urllib.error.URLError, OSError, http.client.HTTPException) as inner:
            return Reply(status, resp_headers, None, time.perf_counter() - start,
                         f"{type(inner).__name__}: {inner}")
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        return Reply(None, {}, None, time.perf_counter() - start,
                     f"{type(exc).__name__}: {exc}")
    elapsed = time.perf_counter() - start
    parsed: Any = None
    if raw:
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
    return Reply(status, resp_headers, parsed, elapsed)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Verify a running AiAdventAgent service (standard library only).")
    parser.add_argument("--url", required=True, help="Agent base URL, e.g. http://192.168.1.10:8001")
    parser.add_argument("--key", default=None, help="API key (or set AIA_API_KEY)")
    parser.add_argument("--n", type=int, default=6, help="number of parallel requests")
    parser.add_argument("--model", default=None, help="model id (with --provider-id)")
    parser.add_argument("--provider-id", type=int, default=None, help="provider id (with --model)")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds per request")
    parser.add_argument("--rate-mode", choices=RATE_MODES, default="chat",
                        help="rate-limit demonstration mode (default: %(default)s)")
    parser.add_argument("--rate-max-attempts", type=int, default=40,
                        help="ceiling of requests in a rate-limit sequence")
    parser.add_argument("--server-rate-limit", type=int, default=None,
                        help="server limit, recorded for information only")
    parser.add_argument("--server-max-concurrent", type=int, default=None,
                        help="server generation cap, recorded for information only")
    parser.add_argument("--out", default=None, help="output directory")
    parser.add_argument("--keep", action="store_true", help="keep the chats the script created")
    return parser


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    """Render a Markdown table."""
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def long_message() -> str:
    """Build a Russian text far above 512 tokens and below the content limit."""
    parts: list[str] = []
    size = 0
    index = 0
    while size < 6500:
        word = _WORDS[index % len(_WORDS)] + str(index // len(_WORDS))
        parts.append(word)
        size += len(word) + 1
        index += 7
    return " ".join(parts)


def body_code(reply: Reply) -> str | None:
    """Return the error code of a response body, if any."""
    if isinstance(reply.body, dict):
        code = reply.body.get("code")
        return str(code) if code is not None else None
    return None


def detail_text(reply: Reply) -> str:
    """Return a short description of a failed reply."""
    if reply.error:
        return reply.error
    if isinstance(reply.body, dict):
        return str(reply.body.get("detail", ""))[:200]
    return str(reply.body or "")[:200]


class Verifier:
    """Runs the verification steps and collects their records."""

    def __init__(self, args: argparse.Namespace, key: str) -> None:
        self.args = args
        self.key = key
        self.url: str = args.url
        self.stamp = utc_now().strftime("%Y%m%d-%H%M%S")
        self.failures: list[str] = []
        self.created: list[dict[str, Any]] = []
        self.model: str | None = None
        self.provider_id: int | None = None
        self.overflow_chat: int | None = None
        self.sections: dict[str, dict[str, Any]] = {
            name: {"ran": False}
            for name in ("reachability", "authentication", "model", "parallel",
                         "overflow", "rate_limit", "cleanup")
        }

    def api(self, method: str, path: str, body: Any = None, key: str | None = "",
            timeout: float | None = None) -> Reply:
        """Call an API path with the run's key unless another key is given."""
        use_key = self.key if key == "" else key
        return call(method, self.url + path, use_key, body,
                    self.args.timeout if timeout is None else timeout)

    def create_chat(self, label: str) -> int | None:
        """Create a chat with a recognizable title and remember it."""
        title = f"verify-service {self.stamp} {label}"
        reply = self.api("POST", "/api/v1/chats", {"title": title}, timeout=60)
        if reply.status in (200, 201) and isinstance(reply.body, dict) and "id" in reply.body:
            chat_id = int(reply.body["id"])
            self.created.append({"id": chat_id, "title": title})
            return chat_id
        say(f"  could not create chat '{label}': status={reply.status} {detail_text(reply)}")
        return None

    def put_settings(self, chat_id: int, payload: dict[str, Any]) -> bool:
        """Apply per-chat settings."""
        reply = self.api("PUT", "/api/v1/settings", {"chat_id": chat_id, **payload}, timeout=60)
        if reply.status != 200:
            say(f"  could not set chat settings: status={reply.status} {detail_text(reply)}")
        return reply.status == 200

    def turn(self, chat_id: int, content: str) -> Reply:
        """Send one chat turn."""
        return self.api("POST", f"/api/v1/chats/{chat_id}/messages",
                        {"content": content, "model": self.model,
                         "provider_id": self.provider_id})

    # ---- preflight -------------------------------------------------------

    def preflight(self) -> bool:
        """Reachability, authentication and model choice; False means exit code 2."""
        say("[1/6] Reachability: GET /health without credentials")
        reply = self.api("GET", "/health", key=None, timeout=15)
        self.sections["reachability"] = {
            "ran": True, "status": reply.status, "elapsed": reply.elapsed,
            "error": reply.error, "ok": reply.status == 200}
        if reply.status != 200:
            say(f"  server unreachable or unhealthy: status={reply.status} {detail_text(reply)}")
            return False
        say(f"  ok: 200 in {reply.elapsed:.2f} s")

        say("[2/6] Authentication: GET /api/v1/auth/me")
        good = self.api("GET", "/api/v1/auth/me", timeout=15)
        bad = self.api("GET", "/api/v1/auth/me", key=INVALID_KEY, timeout=15)
        username = good.body.get("username") if isinstance(good.body, dict) else None
        self.sections["authentication"] = {
            "ran": True, "good_status": good.status, "good_elapsed": good.elapsed,
            "username": username, "bad_status": bad.status, "bad_elapsed": bad.elapsed,
            "ok": good.status == 200 and bad.status == 401}
        if good.status != 200:
            say(f"  the given key was refused: status={good.status}")
            return False
        say(f"  key accepted (user: {username}); wrong key answered {bad.status}")
        if bad.status != 401:
            self.failures.append("wrong key was not refused with 401")
            say("  FAIL: a wrong key must be refused with 401")

        say("[3/6] Model choice")
        return self.choose_model()

    def choose_model(self) -> bool:
        """Use --model/--provider-id or pick a model from the provider list."""
        if self.args.model and self.args.provider_id is not None:
            self.model, self.provider_id = self.args.model, self.args.provider_id
            self.sections["model"] = {"ran": True, "model": self.model,
                                      "provider_id": self.provider_id, "provider": None,
                                      "source": "arguments"}
            say(f"  using model {self.model} of provider {self.provider_id} (from arguments)")
            return True
        reply = self.api("GET", "/api/v1/llm-providers/models", timeout=60)
        groups = reply.body if reply.status == 200 and isinstance(reply.body, list) else []
        pick: tuple[dict[str, Any], str] | None = None
        for group in groups:
            for item in group.get("models") or []:
                if item.get("loaded") is True:
                    pick = (group, item["id"])
                    break
            if pick:
                break
        if pick is None:
            for group in groups:
                if not group.get("error") and group.get("models"):
                    pick = (group, group["models"][0]["id"])
                    break
        if pick is None:
            self.sections["model"] = {"ran": True, "model": None, "status": reply.status}
            say(f"  no usable model (status={reply.status})")
            return False
        group, self.model = pick
        self.provider_id = int(group["provider_id"])
        self.sections["model"] = {"ran": True, "model": self.model,
                                  "provider_id": self.provider_id,
                                  "provider": group.get("name"), "source": "provider list"}
        say(f"  provider {group.get('name')} (id {self.provider_id}), model {self.model}")
        return True

    # ---- parallel --------------------------------------------------------

    def parallel(self) -> None:
        """N parallel chat turns, one chat each."""
        n = self.args.n
        say(f"[4/6] Parallel requests: {n} at once")
        section: dict[str, Any] = {"ran": True, "n": n, "records": []}
        self.sections["parallel"] = section
        chats = [self.create_chat(f"parallel #{k}") for k in range(1, n + 1)]
        if any(c is None for c in chats):
            self.failures.append("could not create all parallel chats")
            section["records"] = []
            return

        def one(index: int, chat_id: int) -> dict[str, Any]:
            reply = self.turn(chat_id, PARALLEL_PROMPT)
            record: dict[str, Any] = {
                "number": index, "chat_id": chat_id, "status": reply.status,
                "code": body_code(reply), "elapsed": reply.elapsed,
                "queue_wait_seconds": None, "reply_chars": None, "reply_start": None,
                "error": None if reply.status == 200 else detail_text(reply)}
            if reply.status == 200 and isinstance(reply.body, dict):
                stats = reply.body.get("stats") or {}
                text = str(reply.body.get("content", ""))
                record.update(queue_wait_seconds=stats.get("queue_wait_seconds"),
                              reply_chars=len(text), reply_start=text[:80])
            say(f"  request {index}: status={reply.status} time={reply.elapsed:.2f} s "
                f"queue_wait={record['queue_wait_seconds']}")
            return record

        with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
            futures = [pool.submit(one, k, int(c)) for k, c in enumerate(chats, start=1) if c]
            records = [f.result() for f in futures]
        records.sort(key=lambda r: r["number"])
        section["records"] = records
        ok = [r for r in records if r["status"] == 200]
        times = [r["elapsed"] for r in records]
        waited = [r for r in ok if (r["queue_wait_seconds"] or 0) > 0]
        section["summary"] = {
            "successes": len(ok), "errors": len(records) - len(ok),
            "min": min(times), "median": statistics.median(times), "max": max(times),
            "queued": len(waited)}
        s = section["summary"]
        say(f"  successes={s['successes']} errors={s['errors']} min={s['min']:.2f} s "
            f"median={s['median']:.2f} s max={s['max']:.2f} s queued={s['queued']}")
        if s["errors"]:
            self.failures.append(f"{s['errors']} parallel request(s) failed")

    # ---- overflow --------------------------------------------------------

    def overflow(self) -> None:
        """Send an oversized message to a no_compression chat with a tiny context."""
        say("[5/6] Context overflow")
        section: dict[str, Any] = {"ran": True}
        self.sections["overflow"] = section
        chat_id = self.create_chat("overflow")
        if chat_id is None or not self.put_settings(
                chat_id, {"strategy": "no_compression", "context_length": 512, "max_tokens": 256}):
            self.failures.append("overflow chat could not be prepared")
            section["ok"] = False
            return
        self.overflow_chat = chat_id
        message = long_message()
        reply = self.turn(chat_id, message)
        suggested = reply.body.get("suggested_strategy") if isinstance(reply.body, dict) else None
        section.update(chat_id=chat_id, status=reply.status, code=body_code(reply),
                       suggested_strategy=suggested, detail=detail_text(reply),
                       elapsed=reply.elapsed, message_chars=len(message))
        section["ok"] = reply.status == 413 and section["code"] == "CONTEXT_OVERFLOW"
        say(f"  status={reply.status} code={section['code']} suggested_strategy={suggested}")
        if not section["ok"]:
            self.failures.append("overflow was not answered with 413 CONTEXT_OVERFLOW")

    # ---- rate limit ------------------------------------------------------

    def sequence(self, mode: str, chat_id: int, send: Any, first_number: int,
                 prior: list[dict[str, Any]]) -> dict[str, Any]:
        """Send requests until a 429 arrives or the attempt ceiling is reached."""
        ceiling = self.args.rate_max_attempts
        result: dict[str, Any] = {"mode": mode, "chat_id": chat_id, "records": list(prior),
                                  "limit_request": None, "retry_after_header": None,
                                  "retry_after_body": None, "code": None, "notes": []}
        start = time.perf_counter()
        number = first_number
        while number <= ceiling:
            reply = send()
            record = {"number": number, "status": reply.status, "code": body_code(reply),
                      "elapsed": reply.elapsed}
            result["records"].append(record)
            if reply.status == 429:
                result["limit_request"] = number
                result["retry_after_header"] = header_value(reply, "Retry-After")
                if isinstance(reply.body, dict):
                    result["retry_after_body"] = reply.body.get("retry_after")
                result["code"] = body_code(reply)
                if result["code"] != "RATE_LIMITED":
                    result["notes"].append(f"unexpected 429 code {result['code']}, expected RATE_LIMITED")
                say(f"  429 on request {number}: Retry-After={result['retry_after_header']} "
                    f"retry_after={result['retry_after_body']} code={result['code']}")
                break
            if reply.status != 200 and not (mode == "fast" and reply.status == 413):
                say(f"  request {number}: status={reply.status} {detail_text(reply)}")
            number += 1
        result["requests_sent"] = len(result["records"])
        result["duration"] = time.perf_counter() - start
        result["reached"] = result["limit_request"] is not None
        if not result["reached"]:
            say(f"  limit not reached after {result['requests_sent']} requests "
                f"in {result['duration']:.1f} s")
            self.failures.append(f"rate limit not reached in mode {mode}")
            if mode == "chat" and result["duration"] > 60:
                result["notes"].append(
                    "the one-minute window rolled over because the model is slow; "
                    "--rate-mode fast shows the limit regardless of model speed")
        limit = self.args.server_rate_limit
        if limit is not None and result["reached"]:
            if result["limit_request"] == limit + 1:
                result["notes"].append(f"429 arrived on request {limit + 1} as expected")
            else:
                result["notes"].append(
                    f"429 expected on request {limit + 1}, arrived on request {result['limit_request']}")
        return result

    def rate_limit(self) -> None:
        """Run the rate-limit demonstration in the chosen mode(s)."""
        mode = self.args.rate_mode
        say(f"[6/6] Rate limit (mode: {mode})")
        section: dict[str, Any] = {"ran": True, "mode": mode, "sequences": []}
        self.sections["rate_limit"] = section
        if mode in ("chat", "both"):
            say("  mode chat: short messages one after another")
            chat_id = self.create_chat("rate")
            if chat_id is None or not self.put_settings(
                    chat_id, {"max_tokens": 256, "system_prompt": RATE_SYSTEM_PROMPT}):
                self.failures.append("rate chat could not be prepared")
                section["sequences"].append({"mode": "chat", "prepared": False, "reached": False})
            else:
                section["sequences"].append(self.sequence(
                    "chat", chat_id, lambda: self.turn(chat_id, RATE_PROMPT), 1, []))
        if mode in ("fast", "both"):
            say("  mode fast: repeated overflow request (request 1 is the overflow demonstration)")
            if self.overflow_chat is None:
                self.failures.append("fast mode needs the overflow chat, which is not available")
                section["sequences"].append({"mode": "fast", "prepared": False, "reached": False})
            else:
                overflow = self.sections["overflow"]
                prior = [{"number": 1, "status": overflow.get("status"),
                          "code": overflow.get("code"), "elapsed": overflow.get("elapsed")}]
                message = long_message()
                chat_id = self.overflow_chat
                section["sequences"].append(self.sequence(
                    "fast", chat_id, lambda: self.turn(chat_id, message), 2, prior))

    # ---- cleanup ---------------------------------------------------------

    def cleanup(self) -> None:
        """Delete the chats this run created, or list them with --keep."""
        section: dict[str, Any] = {"ran": True, "created": len(self.created),
                                   "kept": self.args.keep, "deleted": 0, "chats": self.created}
        self.sections["cleanup"] = section
        if self.args.keep:
            say("Cleanup skipped (--keep); chats left on the server:")
            for chat in self.created:
                say(f"  chat {chat['id']}: {chat['title']}")
            return
        for chat in self.created:
            reply = self.api("DELETE", f"/api/v1/chats/{chat['id']}", timeout=60)
            if reply.status in (200, 204):
                section["deleted"] += 1
            else:
                say(f"  could not delete chat {chat['id']}: status={reply.status}")
        say(f"Cleanup: deleted {section['deleted']} of {len(self.created)} chats")


# ---- output ---------------------------------------------------------------


def fmt(value: Any, digits: int = 2) -> str:
    """Format an optional number for a table."""
    if value is None:
        return "-"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def render_markdown(v: Verifier, meta: dict[str, Any]) -> str:
    """Build the Markdown report."""
    sec = v.sections
    out = ["# Результаты проверки сервиса", "", f"- Адрес: {meta['url']}",
           f"- Начало: {meta['started_at']}", f"- Окончание: {meta['finished_at']}",
           f"- Python: {meta['python_version']}, платформа: {meta['platform']}",
           f"- Скрипт SHA-256: {meta['script_sha256']}",
           f"- Ключ (префикс): {meta['key_prefix']}",
           f"- Модель: {sec['model'].get('model') or NOT_RUN} "
           f"(провайдер: {sec['model'].get('provider') or sec['model'].get('provider_id') or '-'})",
           f"- Режим лимита: {meta['arguments']['rate_mode']}",
           f"- Лимит сервера (справочно): {fmt(meta['server_rate_limit'])}, "
           f"параллельных генераций (справочно): {fmt(meta['server_max_concurrent'])}",
           f"- Код завершения: {meta['exit_code']}", ""]

    out += ["## Доступность и аутентификация", ""]
    rows: list[list[Any]] = []
    r, a = sec["reachability"], sec["authentication"]
    rows.append(["GET /health без ключа", "200", r["status"] if r["ran"] else NOT_RUN,
                 fmt(r.get("elapsed")) if r["ran"] else "-"])
    rows.append(["GET /api/v1/auth/me с ключом", "200",
                 a["good_status"] if a["ran"] else NOT_RUN, fmt(a.get("good_elapsed"))])
    rows.append(["GET /api/v1/auth/me с неверным ключом", "401",
                 a["bad_status"] if a["ran"] else NOT_RUN, fmt(a.get("bad_elapsed"))])
    out += [md_table(["Проверка", "Ожидалось", "Получено", "Время, с"], rows), ""]

    out += ["## Параллельные запросы", ""]
    p = sec["parallel"]
    if not p["ran"]:
        out += [NOT_RUN, ""]
    else:
        prow = [[x["number"], x["chat_id"], x["status"], x["code"] or "-", fmt(x["elapsed"]),
                 fmt(x["queue_wait_seconds"]), fmt(x["reply_chars"])] for x in p["records"]]
        out += [md_table(["№", "Чат", "Статус", "Код", "Время, с", "Ожидание в очереди, с",
                          "Длина ответа"], prow), ""]
        s = p.get("summary")
        if s:
            out += [f"Успешно: {s['successes']}, ошибок: {s['errors']}, "
                    f"минимум: {s['min']:.2f} с, медиана: {s['median']:.2f} с, "
                    f"максимум: {s['max']:.2f} с, ждали в очереди: {s['queued']}.", ""]

    out += ["## Лимит сообщений", ""]
    rl = sec["rate_limit"]
    if not rl["ran"]:
        out += [NOT_RUN, ""]
    else:
        lrows = []
        for q in rl["sequences"]:
            if not q.get("prepared", True):
                lrows.append([q["mode"], "-", "-", "-", "-", "не удалось подготовить чат"])
                continue
            outcome = "лимит достигнут (429)" if q["reached"] else "limit not reached"
            lrows.append([q["mode"], q["requests_sent"], fmt(q["limit_request"]),
                          fmt(q["retry_after_header"]), fmt(q["duration"], 1),
                          outcome + ("; " + "; ".join(q["notes"]) if q["notes"] else "")])
        out += [md_table(["Режим", "Запросов отправлено", "Номер запроса с 429", "Retry-After",
                          "Длительность, с", "Итог"], lrows), ""]

    out += ["## Переполнение контекста", ""]
    o = sec["overflow"]
    if not o["ran"]:
        out += [NOT_RUN, ""]
    else:
        out += [md_table(["Статус", "Код", "Предложенная стратегия", "Время, с"],
                         [[fmt(o.get("status")), o.get("code") or "-",
                           o.get("suggested_strategy") or "-", fmt(o.get("elapsed"))]]), ""]

    out += ["## Очистка", ""]
    c = sec["cleanup"]
    if not c["ran"]:
        out += [NOT_RUN, ""]
    else:
        state = "оставлены (--keep)" if c["kept"] else f"удалено: {c['deleted']}"
        out += [md_table(["Создано чатов", "Состояние"], [[c["created"], state]]), ""]
        if c["kept"]:
            out += [md_table(["Чат", "Название"], [[x["id"], x["title"]] for x in c["chats"]]), ""]
    return "\n".join(out) + "\n"


def write_outputs(v: Verifier, started: datetime, exit_code: int, out_dir: pathlib.Path) -> None:
    """Write verify_results.json and verify_results.md."""
    args = {k: val for k, val in vars(v.args).items() if k != "key"}
    try:
        script_bytes = pathlib.Path(__file__).read_bytes()
    except OSError:
        script_bytes = b""
    meta = {
        "started_at": started.isoformat(), "finished_at": utc_now().isoformat(),
        "url": v.url, "arguments": args, "key_prefix": v.key[:8],
        "python_version": platform.python_version(), "platform": platform.platform(),
        "script_sha256": hashlib.sha256(script_bytes).hexdigest(),
        "server_rate_limit": v.args.server_rate_limit,
        "server_max_concurrent": v.args.server_max_concurrent, "exit_code": exit_code}
    document = {**meta, "failures": v.failures, **v.sections}
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "verify_results.json"
        md_path = out_dir / "verify_results.md"
        json_path.write_text(scrub(json.dumps(document, indent=2, ensure_ascii=False, default=str)),
                             encoding="utf-8")
        md_path.write_text(scrub(render_markdown(v, meta)), encoding="utf-8")
    except OSError as exc:
        say(f"could not write the output files: {exc}")
        return
    say(f"Results written: {md_path} and {json_path}")


def main(argv: list[str] | None = None) -> int:
    """Run the verification and return the exit code."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    args.url = args.url.rstrip("/")
    key = args.key or os.environ.get("AIA_API_KEY") or ""
    if not key:
        say("error: no API key (use --key or the AIA_API_KEY environment variable)")
        return 2
    if args.n < 1 or args.rate_max_attempts < 1 or args.timeout <= 0:
        say("error: --n, --rate-max-attempts and --timeout must be positive")
        return 2
    _SECRETS[:] = [key]
    started = utc_now()
    out_dir = pathlib.Path(args.out or f"verify_out_{started.strftime('%Y%m%d-%H%M%S')}")
    verifier = Verifier(args, key)

    exit_code = 0
    try:
        if not verifier.preflight():
            exit_code = 2
        else:
            try:
                verifier.parallel()
                verifier.overflow()
                verifier.rate_limit()
            finally:
                verifier.cleanup()
            exit_code = 1 if verifier.failures else 0
    finally:
        if verifier.created and not verifier.sections["cleanup"]["ran"]:
            verifier.cleanup()
    for failure in verifier.failures:
        say(f"FAILED: {failure}")
    write_outputs(verifier, started, exit_code, out_dir)
    say("Result: " + ("all expectations met" if exit_code == 0 else f"exit code {exit_code}"))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
