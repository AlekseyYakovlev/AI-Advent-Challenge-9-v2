"""REST tests for /api/v1/llm-providers and provider-aware LM Studio routes."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from httpx import AsyncClient

from shared.config import settings

BASE = "/api/v1/llm-providers"
FOREIGN_ORIGIN = "http://evil.example"
MSG_NOT_FOUND = "Провайдер не найден"
MSG_UNREACHABLE = "Сервер недоступен. Проверьте Base URL и что сервис запущен."
EXAMPLE = "https://api.example.com"


@pytest.fixture
def example_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Declare EXAMPLE_KEY=sk-secret-value in a temporary .env file."""
    file = tmp_path / ".env"
    file.write_text("EXAMPLE_KEY=sk-secret-value\n", encoding="utf-8")
    monkeypatch.setattr(settings, "LLM_PROVIDER_ENV_FILE", str(file))


def _body(**overrides: Any) -> dict[str, Any]:
    """Build a valid create body."""
    body: dict[str, Any] = {
        "name": "Example",
        "base_url": EXAMPLE,
        "api_key_env": "EXAMPLE_KEY",
    }
    body.update(overrides)
    return body


async def _create(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    """Create a provider and return its JSON."""
    resp = await client.post(BASE, json=_body(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _by_name(client: AsyncClient, name: str) -> dict[str, Any]:
    """Return the listed provider with the given name."""
    rows = (await client.get(BASE)).json()
    return next(row for row in rows if row["name"] == name)


# ------------------------------------------------------------------ list / seed


async def test_list_seeds_lm_studio(authenticated_client: AsyncClient) -> None:
    """A fresh user sees only the seeded LM Studio provider, unchecked."""
    resp = await authenticated_client.get(BASE)

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["name"] == "LM Studio"
    assert rows[0]["kind"] == "lm_studio"
    assert rows[0]["api_key_env"] is None
    assert rows[0]["check"]["status"] == "not_checked"


async def test_list_seeds_deepseek_without_leaking_key(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DeepSeek appears when its key resolves and the key value is never serialized."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    resp = await authenticated_client.get(BASE)

    names = [row["name"] for row in resp.json()]
    assert names == ["LM Studio", "DeepSeek"]
    assert resp.json()[1]["api_key_env"] == "DEEPSEEK_API_KEY"
    assert "sk-test" not in resp.text


# ------------------------------------------------------------------ create


async def test_create_normalizes_base_url(authenticated_client: AsyncClient) -> None:
    """Create returns 201 with a normalized URL, enabled and an unchecked state."""
    row = await _create(
        authenticated_client,
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1/",
        api_key_env="OR_KEY",
    )

    assert row["base_url"] == "https://openrouter.ai/api"
    assert row["enabled"] is True
    assert row["kind"] == "openai"
    assert row["check"]["status"] == "not_checked"


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"name": ""}, "Укажите название."),
        ({"base_url": "openrouter.ai"}, "Base URL должен начинаться с http:// или https://."),
        ({"api_key_env": "OR-KEY"}, "Недопустимое имя переменной окружения."),
    ],
)
async def test_create_validation_messages(
    authenticated_client: AsyncClient, overrides: dict[str, Any], message: str
) -> None:
    """Invalid fields return 422 with the Russian message as a plain string."""
    resp = await authenticated_client.post(BASE, json=_body(**overrides))

    assert resp.status_code == 422
    assert resp.json()["detail"] == message


async def test_create_duplicate_name_conflicts(authenticated_client: AsyncClient) -> None:
    """A second provider with the same name returns 409."""
    await _create(authenticated_client)

    resp = await authenticated_client.post(BASE, json=_body())

    assert resp.status_code == 409
    assert resp.json()["detail"] == "Провайдер с таким названием уже есть."


# ------------------------------------------------------------------ update


async def test_update_enabled_keeps_other_fields(authenticated_client: AsyncClient) -> None:
    """A partial update changes only the provided field."""
    row = await _create(authenticated_client)

    resp = await authenticated_client.put(f"{BASE}/{row['id']}", json={"enabled": False})

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["name"] == row["name"]
    assert body["base_url"] == row["base_url"]
    assert body["api_key_env"] == "EXAMPLE_KEY"


async def test_update_empty_api_key_env_clears_it(authenticated_client: AsyncClient) -> None:
    """An explicit empty api_key_env removes the key reference."""
    row = await _create(authenticated_client)

    resp = await authenticated_client.put(f"{BASE}/{row['id']}", json={"api_key_env": ""})

    assert resp.status_code == 200
    assert resp.json()["api_key_env"] is None


async def test_update_null_api_key_env_clears_it(authenticated_client: AsyncClient) -> None:
    """An explicit null api_key_env also removes the key reference."""
    row = await _create(authenticated_client)

    resp = await authenticated_client.put(f"{BASE}/{row['id']}", json={"api_key_env": None})

    assert resp.status_code == 200
    assert resp.json()["api_key_env"] is None


async def test_update_to_existing_name_conflicts(authenticated_client: AsyncClient) -> None:
    """Renaming onto another provider's name returns 409."""
    await authenticated_client.get(BASE)
    row = await _create(authenticated_client)

    resp = await authenticated_client.put(f"{BASE}/{row['id']}", json={"name": "LM Studio"})

    assert resp.status_code == 409


async def test_update_invalid_url_is_422(authenticated_client: AsyncClient) -> None:
    """Partial validation still rejects a bad base_url."""
    row = await _create(authenticated_client)

    resp = await authenticated_client.put(f"{BASE}/{row['id']}", json={"base_url": "nope"})

    assert resp.status_code == 422


# ------------------------------------------------------------------ isolation


async def test_other_user_cannot_see_or_touch_provider(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """Foreign ids return 404 on PUT, DELETE and check; the list is per user."""
    row = await _create(authenticated_client)
    other = second_authenticated_client

    listed = [r["name"] for r in (await other.get(BASE)).json()]
    assert "Example" not in listed

    put = await other.put(f"{BASE}/{row['id']}", json={"name": "Hacked"})
    delete = await other.delete(f"{BASE}/{row['id']}")
    check = await other.post(f"{BASE}/{row['id']}/check")
    missing = await other.put(f"{BASE}/999999", json={"name": "x"})

    for resp in (put, delete, check, missing):
        assert resp.status_code == 404
        assert resp.json()["detail"] == MSG_NOT_FOUND
    assert (await _by_name(authenticated_client, "Example"))["id"] == row["id"]


# ------------------------------------------------------------------ delete


async def test_delete_removes_and_does_not_resurrect(authenticated_client: AsyncClient) -> None:
    """Deleting the seeded LM Studio provider is permanent."""
    seeded = (await authenticated_client.get(BASE)).json()[0]

    resp = await authenticated_client.delete(f"{BASE}/{seeded['id']}")

    assert resp.status_code == 204
    assert (await authenticated_client.get(BASE)).json() == []
    assert (await authenticated_client.get(BASE)).json() == []


# ------------------------------------------------------------------ check


@respx.mock
async def test_check_ok_then_cached_in_list(
    authenticated_client: AsyncClient, example_key: None
) -> None:
    """A successful check reports the model count and the list shows the cached result."""
    row = await _create(authenticated_client)
    respx.get(f"{EXAMPLE}/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})
    )

    resp = await authenticated_client.post(f"{BASE}/{row['id']}/check")

    assert resp.status_code == 200
    assert resp.json()["check"]["status"] == "ok"
    assert resp.json()["check"]["model_count"] == 2
    assert "sk-secret-value" not in resp.text
    cached = await _by_name(authenticated_client, "Example")
    assert cached["check"]["status"] == "ok"
    assert cached["check"]["model_count"] == 2


@respx.mock
async def test_check_bad_key_is_data_not_http_error(
    authenticated_client: AsyncClient, example_key: None
) -> None:
    """A 401 from the provider yields a 200 response carrying an error check."""
    row = await _create(authenticated_client)
    respx.get(f"{EXAMPLE}/v1/models").mock(return_value=httpx.Response(401))

    resp = await authenticated_client.post(f"{BASE}/{row['id']}/check")

    assert resp.status_code == 200
    check = resp.json()["check"]
    assert check["status"] == "error"
    assert check["code"] == "bad_key"
    assert "EXAMPLE_KEY" in check["message"]
    assert "sk-secret-value" not in resp.text


# ------------------------------------------------------------------ models


@respx.mock
async def test_models_groups_with_refresh(authenticated_client: AsyncClient) -> None:
    """Enabled providers produce one group each; failures carry an error string."""
    await authenticated_client.get(BASE)
    failing = await _create(authenticated_client, name="Down", api_key_env=None)
    await _create(authenticated_client, name="Off", base_url="https://off.example.com",
                  api_key_env=None, enabled=False)
    assert failing["enabled"] is True
    respx.get(f"{settings.LM_STUDIO_BASE_URL}/v1/models").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "qwen", "loaded": True}]}
        )
    )
    respx.get(f"{EXAMPLE}/v1/models").mock(side_effect=httpx.ConnectError("refused"))

    resp = await authenticated_client.get(f"{BASE}/models", params={"refresh": "true"})

    assert resp.status_code == 200
    groups = resp.json()
    assert [g["name"] for g in groups] == ["LM Studio", "Down"]
    lm, down = groups
    assert lm["models"] == [{"id": "qwen", "loaded": True}]
    assert lm["error"] is None
    assert down["models"] == []
    assert down["error"] == MSG_UNREACHABLE


# ------------------------------------------------------------------ CSRF


async def test_foreign_origin_rejected_on_mutations(authenticated_client: AsyncClient) -> None:
    """POST, PUT, DELETE and check reject a foreign Origin and change nothing."""
    row = await _create(authenticated_client)
    headers = {"Origin": FOREIGN_ORIGIN}

    responses = [
        await authenticated_client.post(BASE, json=_body(name="New"), headers=headers),
        await authenticated_client.put(
            f"{BASE}/{row['id']}", json={"name": "Hijack"}, headers=headers
        ),
        await authenticated_client.delete(f"{BASE}/{row['id']}", headers=headers),
        await authenticated_client.post(f"{BASE}/{row['id']}/check", headers=headers),
    ]

    assert [r.status_code for r in responses] == [403, 403, 403, 403]
    names = [r["name"] for r in (await authenticated_client.get(BASE)).json()]
    assert "Example" in names
    assert "New" not in names and "Hijack" not in names


@pytest.mark.parametrize("method", ["post", "put"])
async def test_non_json_content_type_rejected(
    authenticated_client: AsyncClient, method: str
) -> None:
    """A JSON body declared as text/plain is refused with 415."""
    row = await _create(authenticated_client)
    url = BASE if method == "post" else f"{BASE}/{row['id']}"
    body = _body(name="Plain") if method == "post" else {"name": "Plain"}

    resp = await authenticated_client.request(
        method.upper(),
        url,
        content=json.dumps(body),
        headers={"Content-Type": "text/plain"},
    )

    assert resp.status_code == 415


# ------------------------------------------------------------------ LM Studio


async def test_lm_studio_load_rejects_non_lm_studio_provider(
    authenticated_client: AsyncClient,
) -> None:
    """A provider_id of an OpenAI-compatible provider returns 400."""
    row = await _create(authenticated_client)

    resp = await authenticated_client.post(
        "/api/v1/lm-studio/load-model", json={"model_id": "m", "provider_id": row["id"]}
    )

    assert resp.status_code == 400


async def test_lm_studio_foreign_provider_is_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    """Another user's provider id is indistinguishable from a missing one."""
    row = await _create(authenticated_client)

    load = await second_authenticated_client.post(
        "/api/v1/lm-studio/load-model", json={"model_id": "m", "provider_id": row["id"]}
    )
    listed = await second_authenticated_client.get(
        "/api/v1/lm-studio/models", params={"provider_id": row["id"]}
    )
    unload = await second_authenticated_client.post(
        "/api/v1/lm-studio/unload-model/m", params={"provider_id": row["id"]}
    )

    assert load.status_code == listed.status_code == unload.status_code == 404


@respx.mock
async def test_lm_studio_models_with_provider_id(authenticated_client: AsyncClient) -> None:
    """Listing with an LM Studio provider id queries that provider's host."""
    seeded = (await authenticated_client.get(BASE)).json()[0]
    respx.get(f"{settings.LM_STUDIO_BASE_URL}/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "qwen", "loaded": False}]})
    )

    resp = await authenticated_client.get(
        "/api/v1/lm-studio/models", params={"provider_id": seeded["id"]}
    )

    assert resp.status_code == 200
    assert resp.json() == [{"id": "qwen", "loaded": False}]


@respx.mock
async def test_lm_studio_models_without_provider_id_still_works(
    authenticated_client: AsyncClient,
) -> None:
    """The legacy call without provider_id uses the configured LM Studio host."""
    respx.get(f"{settings.LM_STUDIO_BASE_URL}/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "x"}]})
    )

    resp = await authenticated_client.get("/api/v1/lm-studio/models")

    assert resp.status_code == 200
    assert resp.json() == [{"id": "x"}]
