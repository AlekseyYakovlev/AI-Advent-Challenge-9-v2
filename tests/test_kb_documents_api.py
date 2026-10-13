"""Tests for GET /api/v1/kb/{kb_id}/files, the listing of a base's source files."""

from httpx import AsyncClient

from agent.kb_api import MSG_KB_NOT_FOUND
from kb_helpers import seed_kb
from shared.models import KbStatus

FILE_KEYS = {"id", "filename", "size_bytes", "page_count"}


async def test_lists_files_sorted_by_filename(authenticated_client: AsyncClient) -> None:
    files = {"b.txt": "second file", "a/c.md": "# nested", "A.txt": "upper"}
    kb_id = await seed_kb(authenticated_client.seeded_user_id, files)
    resp = await authenticated_client.get(f"/api/v1/kb/{kb_id}/files")
    assert resp.status_code == 200
    body = resp.json()
    assert [f["filename"] for f in body] == sorted(files)
    for item in body:
        assert set(item) == FILE_KEYS
        assert item["size_bytes"] == len(files[item["filename"]].encode("utf-8"))


async def test_empty_base_returns_empty_list(authenticated_client: AsyncClient) -> None:
    kb_id = await seed_kb(authenticated_client.seeded_user_id, {})
    resp = await authenticated_client.get(f"/api/v1/kb/{kb_id}/files")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_lists_files_of_ready_base(authenticated_client: AsyncClient) -> None:
    kb_id = await seed_kb(authenticated_client.seeded_user_id, status=KbStatus.READY)
    resp = await authenticated_client.get(f"/api/v1/kb/{kb_id}/files")
    assert resp.status_code == 200
    assert [f["filename"] for f in resp.json()] == ["doc.txt"]


async def test_missing_kb_returns_404(authenticated_client: AsyncClient) -> None:
    resp = await authenticated_client.get("/api/v1/kb/9999/files")
    assert resp.status_code == 404
    assert resp.json()["detail"] == MSG_KB_NOT_FOUND


async def test_foreign_kb_returns_404(
    authenticated_client: AsyncClient, second_authenticated_client: AsyncClient
) -> None:
    kb_id = await seed_kb(authenticated_client.seeded_user_id)
    resp = await second_authenticated_client.get(f"/api/v1/kb/{kb_id}/files")
    assert resp.status_code == 404


async def test_unauthenticated_gets_401(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/kb/1/files")
    assert resp.status_code == 401
