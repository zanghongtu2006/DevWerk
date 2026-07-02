from __future__ import annotations

from fastapi import APIRouter

from app.services.capability_catalog import capability_catalog


router = APIRouter(prefix="/capabilities", tags=["Capabilities"])


@router.get("")
def capabilities_list():
    return capability_catalog()
