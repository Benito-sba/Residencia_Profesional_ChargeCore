from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.database import get_collection


OCPPProtocol = Literal["ocpp1.6", "ocpp2.0.1", "ocpp2.1"]
OCPP_PROTOCOL_SETTINGS_ID = "ocpp_protocols"
OCPP_PROTOCOL_SETTINGS_DEFAULTS = {
    "_id": OCPP_PROTOCOL_SETTINGS_ID,
    "automatic_detection": True,
    "enabled_protocols": ["ocpp1.6", "ocpp2.0.1", "ocpp2.1"],
    "preferred_protocol": "ocpp1.6",
    "revision": 0,
}
OCPP_16_SUBPROTOCOLS = ("ocpp1.6", "ocpp1.6j")


class OCPPProtocolSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    automatic_detection: bool = True
    enabled_protocols: list[OCPPProtocol] = Field(
        default_factory=lambda: ["ocpp1.6", "ocpp2.0.1", "ocpp2.1"]
    )
    preferred_protocol: OCPPProtocol | None = "ocpp1.6"
    revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_protocol_selection(self):
        if not self.enabled_protocols:
            raise ValueError("Debe habilitarse al menos una versión OCPP")
        if len(self.enabled_protocols) != len(set(self.enabled_protocols)):
            raise ValueError("enabled_protocols no puede contener duplicados")
        if self.preferred_protocol is not None and (
            self.preferred_protocol not in self.enabled_protocols
        ):
            raise ValueError("La versión preferida debe estar habilitada")
        if not self.automatic_detection and self.preferred_protocol is None:
            raise ValueError(
                "La versión preferida es obligatoria con detección automática desactivada"
            )
        return self


class OCPPProtocolSettingsConflict(Exception):
    pass


async def get_ocpp_protocol_settings() -> OCPPProtocolSettings:
    collection = get_collection("system_settings")
    await collection.update_one(
        {"_id": OCPP_PROTOCOL_SETTINGS_ID},
        {"$setOnInsert": OCPP_PROTOCOL_SETTINGS_DEFAULTS},
        upsert=True,
    )
    document = await collection.find_one({"_id": OCPP_PROTOCOL_SETTINGS_ID})
    if document is None:
        raise RuntimeError("No se pudo leer la configuración persistida de OCPP")
    return OCPPProtocolSettings.model_validate(document)


async def save_ocpp_protocol_settings(
    settings: OCPPProtocolSettings,
) -> OCPPProtocolSettings:
    collection = get_collection("system_settings")
    await collection.update_one(
        {"_id": OCPP_PROTOCOL_SETTINGS_ID},
        {"$setOnInsert": OCPP_PROTOCOL_SETTINGS_DEFAULTS},
        upsert=True,
    )
    result = await collection.update_one(
        {
            "_id": OCPP_PROTOCOL_SETTINGS_ID,
            "revision": settings.revision,
        },
        {
            "$set": {
                "automatic_detection": settings.automatic_detection,
                "enabled_protocols": settings.enabled_protocols,
                "preferred_protocol": settings.preferred_protocol,
            },
            "$inc": {"revision": 1},
        },
    )
    if result.matched_count != 1:
        raise OCPPProtocolSettingsConflict(
            "La configuración OCPP cambió en otra sesión; vuelve a cargarla."
        )
    return await get_ocpp_protocol_settings()


async def count_ocpp_connections_by_version() -> dict[str, int]:
    chargers = get_collection("cargadores")
    counts = {}
    for protocol in ("ocpp1.6", "ocpp2.0.1", "ocpp2.1"):
        subprotocols = (
            OCPP_16_SUBPROTOCOLS if protocol == "ocpp1.6" else (protocol,)
        )
        counts[protocol] = await chargers.count_documents({
            "conectado": True,
            "subprotocolo_ocpp": {"$in": subprotocols},
        })
    return counts
