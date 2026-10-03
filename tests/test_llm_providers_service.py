"""Tests for provider seeding, resolution, validation and connection checks."""

import asyncio
from pathlib import Path

import httpx
import pytest
import respx

from agent import providers
from agent.providers import (
    MODEL_CACHE,
    ProviderNameConflictError,
    ProviderUnavailableError,
    ProviderValidationError,
)
from agent.schemas import normalize_base_url
from shared.config import settings
from shared.database import async_session_factory
from shared.models import LlmProvider
from tests.conftest import _create_user

BASE = "https://api.example.com"


@pytest.fixture
def example_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Declare EXAMPLE_KEY=sk-x in a temporary .env file."""
    file = tmp_path / ".env"
    file.write_text("EXAMPLE_KEY=sk-x\n", encoding="utf-8")
    monkeypatch.setattr(settings, "LLM_PROVIDER_ENV_FILE", str(file))


async def _seeded_rows(user_id: int) -> list[LlmProvider]:
    async with async_session_factory() as session:
        await providers.ensure_seeded(session, user_id)
        return await providers.list_providers(session, user_id)


async def _make_provider(
    user_id: int,
    name: str = "Example",
    api_key_env: str | None = "EXAMPLE_KEY",
    base_url: str = BASE,
    enabled: bool = True,
) -> LlmProvider:
    async with async_session_factory() as session:
        row = await providers.create_provider(
            session,
            user_id,
            {"name": name, "base_url": base_url, "api_key_env": api_key_env, "enabled": enabled},
        )
    return row


# ---------------------------------------------------------------- seeding


async def test_seed_lm_studio_only() -> None:
    """Without a DeepSeek key only the LM Studio provider is seeded."""
    uid = await _create_user("s1", "pw")
    rows = await _seeded_rows(uid)
    assert len(rows) == 1
    row = rows[0]
    assert row.name == "LM Studio"
    assert row.kind == "lm_studio"
    assert row.base_url == normalize_base_url(settings.LM_STUDIO_BASE_URL)
    assert row.api_key_env is None
    assert row.enabled is True


async def test_seed_deepseek_when_key_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """A resolvable DeepSeek key adds the DeepSeek provider."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    uid = await _create_user("s2", "pw")
    rows = await _seeded_rows(uid)
    assert [r.name for r in rows] == ["LM Studio", "DeepSeek"]
    ds = rows[1]
    assert ds.base_url == "https://api.deepseek.com"
    assert ds.kind == "openai"
    assert ds.api_key_env == "DEEPSEEK_API_KEY"


async def test_seed_is_idempotent() -> None:
    """Repeated seeding leaves one row per seed."""
    uid = await _create_user("s3", "pw")
    for _ in range(3):
        rows = await _seeded_rows(uid)
    assert len(rows) == 1


async def test_seed_concurrent_calls() -> None:
    """Concurrent seeding on separate sessions creates each seed once."""
    uid = await _create_user("s4", "pw")

    async def seed() -> None:
        async with async_session_factory() as session:
            await providers.ensure_seeded(session, uid)

    await asyncio.gather(seed(), seed())
    assert len(await _seeded_rows(uid)) == 1


async def test_seed_idempotent_across_restart() -> None:
    """After a restart (reset_state) existing rows are not duplicated."""
    uid = await _create_user("s5", "pw")
    await _seeded_rows(uid)
    providers.reset_state()
    assert len(await _seeded_rows(uid)) == 1


async def test_deleted_seed_not_resurrected() -> None:
    """A deleted seeded provider stays deleted after a simulated restart."""
    uid = await _create_user("s6", "pw")
    rows = await _seeded_rows(uid)
    async with async_session_factory() as session:
        row = await providers.get_provider(session, uid, rows[0].id)
        assert row is not None
        await providers.delete_provider(session, row)
    providers.reset_state()
    assert await _seeded_rows(uid) == []


async def test_deepseek_seeded_when_key_appears_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DeepSeek row appears on the next call once the key becomes available."""
    uid = await _create_user("s7", "pw")
    assert len(await _seeded_rows(uid)) == 1
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    rows = await _seeded_rows(uid)
    assert [r.name for r in rows] == ["LM Studio", "DeepSeek"]


async def test_seed_survives_name_collision() -> None:
    """A user-made provider named 'LM Studio' does not break seeding."""
    uid = await _create_user("s8", "pw")
    await _make_provider(uid, name="LM Studio", api_key_env=None, base_url="http://x")
    rows = await _seeded_rows(uid)
    assert len(rows) == 1
    assert rows[0].kind == "openai"


# ---------------------------------------------------------------- resolver


async def test_get_provider_row_none_is_lm_studio() -> None:
    """provider_id None selects the seeded LM Studio row."""
    uid = await _create_user("r1", "pw")
    async with async_session_factory() as session:
        row = await providers.get_provider_row(session, uid, None)
    assert row.name == "LM Studio"


async def test_get_provider_row_foreign_missing_disabled() -> None:
    """Foreign, nonexistent and disabled providers are unavailable."""
    owner = await _create_user("r2a", "pw")
    other = await _create_user("r2b", "pw")
    foreign = await _make_provider(owner, name="Mine")
    disabled = await _make_provider(other, name="Off", enabled=False)
    async with async_session_factory() as session:
        with pytest.raises(ProviderUnavailableError) as foreign_exc:
            await providers.get_provider_row(session, other, foreign.id)
        assert foreign_exc.value.name is None
        with pytest.raises(ProviderUnavailableError) as missing_exc:
            await providers.get_provider_row(session, other, 9999)
        assert missing_exc.value.name is None
        with pytest.raises(ProviderUnavailableError) as disabled_exc:
            await providers.get_provider_row(session, other, disabled.id)
        assert disabled_exc.value.name == "Off"
        assert "Off" in disabled_exc.value.message


async def test_resolve_client_legacy_without_user() -> None:
    """An unowned chat resolves to a keyless LM Studio client."""
    client, row = await providers.resolve_client(None, None)
    assert client._base_url == settings.LM_STUDIO_BASE_URL.rstrip("/")
    assert client._api_key == ""
    assert row is None


async def test_resolve_client_for_user_default() -> None:
    """A user with provider None gets the LM Studio row and a keyless client."""
    uid = await _create_user("r3", "pw")
    client, row = await providers.resolve_client(uid, None)
    assert row is not None and row.name == "LM Studio"
    assert client._api_key == ""


async def test_build_client_uses_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_client resolves the key from the env variable name."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    uid = await _create_user("r4", "pw")
    rows = await _seeded_rows(uid)
    ds = rows[1]
    client = providers.build_client(ds)
    assert client._api_key == "sk-test"
    assert client._base_url == ds.base_url


# ---------------------------------------------------------------- validation


def test_validate_name_required() -> None:
    """An empty name is rejected."""
    with pytest.raises(ProviderValidationError) as exc:
        providers.validate_provider_fields("", "https://x", None, partial=False)
    assert exc.value.message == "Укажите название."


def test_validate_name_too_long() -> None:
    """A name over 100 characters is rejected."""
    with pytest.raises(ProviderValidationError):
        providers.validate_provider_fields("a" * 101, "https://x", None, partial=False)


@pytest.mark.parametrize("url", ["ftp://x", "x", ""])
def test_validate_bad_url(url: str) -> None:
    """Non-http(s) URLs are rejected."""
    with pytest.raises(ProviderValidationError) as exc:
        providers.validate_provider_fields("n", url, None, partial=False)
    assert exc.value.message == "Base URL должен начинаться с http:// или https://."


def test_validate_bad_env_name() -> None:
    """A malformed env variable name is rejected."""
    with pytest.raises(ProviderValidationError) as exc:
        providers.validate_provider_fields("n", "https://x", "MY-KEY", partial=False)
    assert exc.value.message == "Недопустимое имя переменной окружения."


def test_validate_blank_env_is_none() -> None:
    """A blank env name is stored as None."""
    cleaned = providers.validate_provider_fields("n", "https://x", "  ", partial=False)
    assert cleaned["api_key_env"] is None


def test_validate_url_normalized() -> None:
    """The base URL is normalized."""
    cleaned = providers.validate_provider_fields(
        "n", "https://api.openai.com/v1/", None, partial=False
    )
    assert cleaned["base_url"] == "https://api.openai.com"


def test_validate_partial_returns_only_provided() -> None:
    """Partial validation returns only provided keys; empty env clears."""
    assert providers.validate_provider_fields(" N ", None, None, partial=True) == {"name": "N"}
    assert providers.validate_provider_fields(None, None, "", partial=True) == {
        "api_key_env": None
    }


async def test_create_duplicate_name_conflicts() -> None:
    """The same name twice for one user conflicts; two users may share a name."""
    a = await _create_user("v1", "pw")
    b = await _create_user("v2", "pw")
    await _make_provider(a, name="Dup")
    with pytest.raises(ProviderNameConflictError):
        await _make_provider(a, name="Dup")
    await _make_provider(b, name="Dup")


# ---------------------------------------------------------------- check


async def test_check_ok(example_key: None) -> None:
    """A 200 model list yields ok, sends the bearer key and caches the result."""
    uid = await _create_user("c1", "pw")
    row = await _make_provider(uid)
    with respx.mock:
        route = respx.get(f"{BASE}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]}),
        )
        result = await providers.check_provider(row)
    assert result.status == "ok"
    assert [m["id"] for m in result.models] == ["m1", "m2"]
    assert route.calls.last.request.headers["Authorization"] == "Bearer sk-x"
    assert MODEL_CACHE[(uid, row.id)] is result
    assert providers.cached_check(row) is result
    assert "sk-x" not in repr(result)


@pytest.mark.parametrize("status", [401, 403])
async def test_check_bad_key(example_key: None, status: int) -> None:
    """401 and 403 map to bad_key and name the variable."""
    uid = await _create_user("c2", "pw")
    row = await _make_provider(uid)
    with respx.mock:
        respx.get(f"{BASE}/v1/models").mock(return_value=httpx.Response(status))
        result = await providers.check_provider(row)
    assert result.code == "bad_key"
    assert "EXAMPLE_KEY" in (result.message or "")
    assert "sk-x" not in repr(result)


async def test_check_bad_key_without_env_requires_key() -> None:
    """A 401 for a keyless provider asks for a key variable."""
    uid = await _create_user("c2b", "pw")
    row = await _make_provider(uid, api_key_env=None)
    with respx.mock:
        respx.get(f"{BASE}/v1/models").mock(return_value=httpx.Response(401))
        result = await providers.check_provider(row)
    assert result.code == "bad_key"
    assert result.message == providers.MSG_KEY_REQUIRED


async def test_check_unreachable(example_key: None) -> None:
    """A connection error maps to unreachable."""
    uid = await _create_user("c3", "pw")
    row = await _make_provider(uid)
    with respx.mock:
        respx.get(f"{BASE}/v1/models").mock(side_effect=httpx.ConnectError("boom"))
        result = await providers.check_provider(row)
    assert result.code == "unreachable"


async def test_check_timeout(example_key: None) -> None:
    """A read timeout maps to timeout."""
    uid = await _create_user("c4", "pw")
    row = await _make_provider(uid)
    with respx.mock:
        respx.get(f"{BASE}/v1/models").mock(side_effect=httpx.ReadTimeout("slow"))
        result = await providers.check_provider(row)
    assert result.code == "timeout"


async def test_check_http_error(example_key: None) -> None:
    """A 500 maps to http and mentions the status."""
    uid = await _create_user("c5", "pw")
    row = await _make_provider(uid)
    with respx.mock:
        respx.get(f"{BASE}/v1/models").mock(return_value=httpx.Response(500))
        result = await providers.check_provider(row)
    assert result.code == "http"
    assert "500" in (result.message or "")


async def test_check_redirect_not_followed(example_key: None) -> None:
    """A 3xx is reported as http and the redirect target is never requested."""
    uid = await _create_user("c5b", "pw")
    row = await _make_provider(uid)
    with respx.mock:
        respx.get(f"{BASE}/v1/models").mock(
            return_value=httpx.Response(302, headers={"Location": "https://evil.example/x"}),
        )
        target = respx.get("https://evil.example/x").mock(return_value=httpx.Response(200))
        result = await providers.check_provider(row)
    assert result.code == "http"
    assert not target.called


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"data": 5}),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json={"data": [{"name": "x"}]}),
    ],
)
async def test_check_bad_response(example_key: None, response: httpx.Response) -> None:
    """Unparseable or wrongly shaped bodies map to bad_response."""
    uid = await _create_user("c6", "pw")
    row = await _make_provider(uid)
    with respx.mock:
        respx.get(f"{BASE}/v1/models").mock(return_value=response)
        result = await providers.check_provider(row)
    assert result.code == "bad_response"


async def test_check_env_missing_makes_no_request() -> None:
    """An unresolvable key variable fails before any HTTP call."""
    uid = await _create_user("c7", "pw")
    row = await _make_provider(uid, api_key_env="NOT_DECLARED_KEY")
    with respx.mock:
        route = respx.get(f"{BASE}/v1/models").mock(return_value=httpx.Response(200))
        result = await providers.check_provider(row)
    assert result.code == "env_missing"
    assert "NOT_DECLARED_KEY" in (result.message or "")
    assert not route.called


async def test_check_without_key_sends_no_auth_header() -> None:
    """A provider without a key variable sends no Authorization header."""
    uid = await _create_user("c8", "pw")
    row = await _make_provider(uid, api_key_env=None)
    with respx.mock:
        route = respx.get(f"{BASE}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []}),
        )
        result = await providers.check_provider(row)
    assert result.status == "ok"
    assert "Authorization" not in route.calls.last.request.headers


async def test_check_lm_studio_reports_loaded() -> None:
    """An LM Studio row reports the loaded flag."""
    uid = await _create_user("c9", "pw")
    rows = await _seeded_rows(uid)
    lm = rows[0]
    with respx.mock:
        respx.get(f"{lm.base_url}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "q", "loaded": True}]}),
        )
        result = await providers.check_provider(lm)
    assert result.models == [{"id": "q", "loaded": True}]


async def test_check_lm_studio_adds_model_types() -> None:
    """LM Studio models carry `type` when /api/v0/models is reachable."""
    uid = await _create_user("c10", "pw")
    lm = (await _seeded_rows(uid))[0]
    with respx.mock:
        respx.get(f"{lm.base_url}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "e"}, {"id": "q"}]}),
        )
        respx.get(f"{lm.base_url}/api/v0/models").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"id": "e", "type": "embeddings"}, {"id": "q", "type": "llm"}]},
            ),
        )
        result = await providers.check_provider(lm)
    assert result.status == "ok"
    assert {m["id"]: m.get("type") for m in result.models} == {"e": "embeddings", "q": "llm"}


async def test_check_lm_studio_types_failure_keeps_models() -> None:
    """A failing /api/v0/models leaves the list intact, without `type`, status ok."""
    uid = await _create_user("c11", "pw")
    lm = (await _seeded_rows(uid))[0]
    with respx.mock:
        respx.get(f"{lm.base_url}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "q"}]}),
        )
        respx.get(f"{lm.base_url}/api/v0/models").mock(return_value=httpx.Response(404))
        result = await providers.check_provider(lm)
    assert result.status == "ok"
    assert result.models == [{"id": "q", "loaded": False}]


# ---------------------------------------------------------------- groups & cache


async def test_model_groups_ok_failing_disabled(example_key: None) -> None:
    """Disabled providers are omitted; failing ones carry an error and no models."""
    uid = await _create_user("g1", "pw")
    await _make_provider(uid, name="Good")
    await _make_provider(uid, name="Bad", base_url="https://bad.example.com", api_key_env=None)
    await _make_provider(uid, name="Off", base_url="https://off.example.com", enabled=False)
    with respx.mock:
        respx.get(f"{BASE}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "a"}]}),
        )
        respx.get("https://bad.example.com/v1/models").mock(return_value=httpx.Response(500))
        respx.get(f"{settings.LM_STUDIO_BASE_URL.rstrip('/')}/v1/models").mock(
            return_value=httpx.Response(500),
        )
        async with async_session_factory() as session:
            groups = await providers.list_model_groups(session, uid, refresh=True)
    names = [g["name"] for g in groups]
    assert "Off" not in names
    assert set(names) == {"LM Studio", "Good", "Bad"}
    good = next(g for g in groups if g["name"] == "Good")
    bad = next(g for g in groups if g["name"] == "Bad")
    assert good["models"] == [{"id": "a", "loaded": None}]
    assert good["error"] is None
    assert bad["models"] == []
    assert bad["error"] == providers.MSG_HTTP.format(status=500)


async def test_model_groups_cached_without_refresh(example_key: None) -> None:
    """refresh=False serves the cache without new HTTP calls."""
    uid = await _create_user("g2", "pw")
    await _make_provider(uid, name="Good")
    with respx.mock:
        route = respx.get(f"{BASE}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "a"}]}),
        )
        respx.get(f"{settings.LM_STUDIO_BASE_URL.rstrip('/')}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []}),
        )
        async with async_session_factory() as session:
            await providers.list_model_groups(session, uid, refresh=True)
            calls = route.call_count
            await providers.list_model_groups(session, uid, refresh=False)
    assert route.call_count == calls == 1


async def test_update_and_delete_drop_cache(example_key: None) -> None:
    """Editing or deleting a provider drops its cached check."""
    uid = await _create_user("g3", "pw")
    row = await _make_provider(uid)
    with respx.mock:
        respx.get(f"{BASE}/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []}),
        )
        await providers.check_provider(row)
    assert (uid, row.id) in MODEL_CACHE
    async with async_session_factory() as session:
        fresh = await providers.get_provider(session, uid, row.id)
        assert fresh is not None
        await providers.update_provider(session, fresh, {"name": "Renamed"})
        assert (uid, row.id) not in MODEL_CACHE
        MODEL_CACHE[(uid, row.id)] = providers.CheckResult(
            "ok", None, None, [], fresh.updated_at
        )
        await providers.delete_provider(session, fresh)
    assert (uid, row.id) not in MODEL_CACHE
