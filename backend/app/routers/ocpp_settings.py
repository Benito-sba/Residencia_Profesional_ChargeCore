from fastapi import APIRouter, Depends, HTTPException
from pydantic import ConfigDict

from app.auth import require_superadmin
from app.services.ocpp_protocol_settings import (
    OCPPProtocolSettings,
    OCPPProtocolSettingsConflict,
    count_ocpp_connections_by_version,
    get_ocpp_protocol_settings,
    save_ocpp_protocol_settings,
)


router = APIRouter(prefix="/api/ocpp-settings", tags=["Configuración OCPP"])


class OCPPProtocolSettingsUpdate(OCPPProtocolSettings):
    model_config = ConfigDict(extra="forbid")


async def _settings_response(configuration: OCPPProtocolSettings) -> dict:
    return {
        **configuration.model_dump(),
        "connections_by_version": await count_ocpp_connections_by_version(),
    }


@router.get("/protocols")
async def read_protocol_settings(current_user=Depends(require_superadmin)):
    configuration = await get_ocpp_protocol_settings()
    return await _settings_response(configuration)


@router.put("/protocols")
async def update_protocol_settings(
    request: OCPPProtocolSettingsUpdate,
    current_user=Depends(require_superadmin),
):
    try:
        configuration = await save_ocpp_protocol_settings(
            OCPPProtocolSettings.model_validate(request.model_dump())
        )
    except OCPPProtocolSettingsConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return await _settings_response(configuration)
