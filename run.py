"""Entry point: start the UI server (agent is launched by the supervisor)."""

import uvicorn

from shared.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "ui.main:app",
        host="127.0.0.1",
        port=settings.UI_PORT,
        reload=False,
    )
