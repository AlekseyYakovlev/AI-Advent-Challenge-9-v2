"""REST route exposing where the app is reachable on the network (/api/v1/network)."""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agent.dependencies import get_current_user
from shared.config import settings
from shared.logger import get_logger
from shared.models import User
from shared.network import reachable_urls

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/network", tags=["network"])


class NetworkInfoOut(BaseModel):
    """Exposure mode and the addresses the app is reachable at."""

    mode: Literal["loopback", "lan"]
    addresses: list[str]


@router.get("", response_model=NetworkInfoOut)
async def get_network_info(
    current_user: User = Depends(get_current_user),
) -> NetworkInfoOut:
    """Return the exposure mode; addresses are disclosed only in LAN mode."""
    if not settings.is_lan_mode:
        return NetworkInfoOut(mode="loopback", addresses=[])
    addresses = await asyncio.to_thread(reachable_urls, settings.UI_PORT)
    return NetworkInfoOut(mode="lan", addresses=addresses)
