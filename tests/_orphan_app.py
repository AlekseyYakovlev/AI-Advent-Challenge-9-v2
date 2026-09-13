"""Minimal FastAPI app used as an orphan process in supervisor tests."""

from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
async def health() -> dict[str, str]:
    """Stub health endpoint for orphan detection tests."""
    return {"status": "orphan"}
