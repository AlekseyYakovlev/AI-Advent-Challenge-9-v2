"""UI FastAPI application: static files and agent supervisor."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from shared.config import settings
from shared.logger import get_logger
from ui.supervisor import AgentSupervisor

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

CORS_ORIGINS = [
    f"http://localhost:{settings.UI_PORT}",
    f"http://127.0.0.1:{settings.UI_PORT}",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start supervisor on boot; stop agent on shutdown."""
    supervisor = AgentSupervisor(settings.AGENT_PORT, settings.DB_PATH)
    app.state.supervisor = supervisor
    logger.info("ui_starting", port=settings.UI_PORT)
    await supervisor.start()
    yield
    logger.info("ui_shutting_down")
    await supervisor.stop()


app = FastAPI(title="AI Agent UI", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root() -> FileResponse:
    """Serve the main UI page."""
    return FileResponse(STATIC_DIR / "index.html")
