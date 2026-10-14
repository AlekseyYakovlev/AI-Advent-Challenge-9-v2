"""Folder picker module (fake subprocess) and the session-only pick-folder route."""

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from agent import folder_picker, review_api
from agent.folder_picker import FolderPickerError
from agent.main import app

PICK = "/api/v1/reviews/pick-folder"


class FakeProc:
    def __init__(self, stdout: bytes = b"", returncode: int = 0, hang: bool = False) -> None:
        self.pid = 4242
        self.returncode: int | None = returncode
        self._stdout = stdout
        self._hang = hang
        self.waited = False

    async def communicate(self) -> tuple[bytes, None]:
        if self._hang:
            await asyncio.Event().wait()
        return self._stdout, None

    async def wait(self) -> int:
        self.waited = True
        return 0


def api_client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def make_key(client: AsyncClient) -> dict[str, str]:
    resp = await client.post("/api/v1/api-keys", json={"name": "t"})
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['key']}"}


@pytest.fixture(autouse=True)
def lock_free() -> None:
    assert not review_api._pick_lock.locked()


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(folder_picker.sys, "platform", "win32")


def fake_spawn(monkeypatch: pytest.MonkeyPatch, proc: FakeProc) -> list[list[str]]:
    calls: list[list[str]] = []

    async def spawn(argv: list[str]) -> FakeProc:
        calls.append(argv)
        return proc

    monkeypatch.setattr(folder_picker, "_spawn", spawn)
    return calls


async def test_returns_path_with_cyrillic(monkeypatch: pytest.MonkeyPatch, windows: None) -> None:
    fake_spawn(monkeypatch, FakeProc("C:\\Projects\\Демо\r\n".encode("utf-8")))
    assert await folder_picker.pick_folder() == "C:\\Projects\\Демо"


async def test_cancel_returns_none(monkeypatch: pytest.MonkeyPatch, windows: None) -> None:
    fake_spawn(monkeypatch, FakeProc(b""))
    assert await folder_picker.pick_folder() is None


async def test_nonzero_exit_is_unavailable(monkeypatch: pytest.MonkeyPatch, windows: None) -> None:
    fake_spawn(monkeypatch, FakeProc(b"", returncode=1))
    with pytest.raises(FolderPickerError) as info:
        await folder_picker.pick_folder()
    assert info.value.code == "FOLDER_PICKER_UNAVAILABLE"


async def test_spawn_oserror_is_unavailable(monkeypatch: pytest.MonkeyPatch, windows: None) -> None:
    async def spawn(argv: list[str]) -> FakeProc:
        raise OSError("missing")

    monkeypatch.setattr(folder_picker, "_spawn", spawn)
    with pytest.raises(FolderPickerError) as info:
        await folder_picker.pick_folder()
    assert info.value.code == "FOLDER_PICKER_UNAVAILABLE"


async def test_timeout_kills_process(monkeypatch: pytest.MonkeyPatch, windows: None) -> None:
    proc = FakeProc(hang=True)
    fake_spawn(monkeypatch, proc)
    killed: list[int] = []
    monkeypatch.setattr(folder_picker, "_kill_tree", killed.append)
    monkeypatch.setattr(folder_picker, "PICK_FOLDER_TIMEOUT", 0.05)
    with pytest.raises(FolderPickerError) as info:
        await folder_picker.pick_folder()
    assert info.value.code == "FOLDER_PICKER_TIMEOUT"
    assert killed == [4242]
    assert proc.waited


async def test_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(folder_picker.sys, "platform", "linux")
    calls = fake_spawn(monkeypatch, FakeProc())
    with pytest.raises(FolderPickerError) as info:
        await folder_picker.pick_folder()
    assert info.value.code == "FOLDER_PICKER_UNSUPPORTED"
    assert calls == []


def test_argv_is_fixed() -> None:
    argv = folder_picker._build_argv()
    assert {"-STA", "-NoProfile", "-EncodedCommand"} <= set(argv)
    assert argv[-2] == "-EncodedCommand"


def patch_pick(monkeypatch: pytest.MonkeyPatch, result: Any = None, raises: Exception | None = None) -> list[int]:
    calls: list[int] = []

    async def fake() -> str | None:
        calls.append(1)
        if raises is not None:
            raise raises
        return result

    monkeypatch.setattr(review_api.folder_picker, "pick_folder", fake)
    return calls


async def test_route_anonymous_401() -> None:
    async with api_client() as client:
        resp = await client.post(PICK, json={})
    assert resp.status_code == 401


async def test_route_api_key_403(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = patch_pick(monkeypatch, "C:\\x")
    async with api_client() as client:
        headers = await make_key(authenticated_client)
        resp = await client.post(PICK, json={}, headers=headers)
    assert resp.status_code == 403
    assert calls == []


async def test_route_returns_path_and_null(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_pick(monkeypatch, "C:\\repo")
    resp = await authenticated_client.post(PICK, json={})
    assert resp.status_code == 200
    assert resp.json() == {"path": "C:\\repo"}
    patch_pick(monkeypatch, None)
    resp = await authenticated_client.post(PICK, json={})
    assert resp.status_code == 200
    assert resp.json() == {"path": None}


@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        ("FOLDER_PICKER_TIMEOUT", 504),
        ("FOLDER_PICKER_UNAVAILABLE", 503),
        ("FOLDER_PICKER_UNSUPPORTED", 501),
    ],
)
async def test_route_error_codes(
    authenticated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    code: str,
    status_code: int,
) -> None:
    patch_pick(monkeypatch, raises=FolderPickerError(code, "msg"))
    resp = await authenticated_client.post(PICK, json={})
    assert resp.status_code == status_code
    assert resp.json()["code"] == code
    assert not review_api._pick_lock.locked()


async def test_route_busy_409(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = asyncio.Event()

    async def fake() -> str | None:
        await gate.wait()
        return "C:\\repo"

    monkeypatch.setattr(review_api.folder_picker, "pick_folder", fake)
    first = asyncio.create_task(authenticated_client.post(PICK, json={}))
    for _ in range(200):
        if review_api._pick_lock.locked():
            break
        await asyncio.sleep(0.01)
    assert review_api._pick_lock.locked()
    second = await authenticated_client.post(PICK, json={})
    assert second.status_code == 409
    assert second.json()["code"] == "FOLDER_PICKER_BUSY"
    gate.set()
    resp = await first
    assert resp.status_code == 200
    assert not review_api._pick_lock.locked()


async def test_route_foreign_origin_403(
    authenticated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = patch_pick(monkeypatch, "C:\\x")
    resp = await authenticated_client.post(
        PICK, json={}, headers={"Origin": "https://evil.example"}
    )
    assert resp.status_code == 403
    assert calls == []
