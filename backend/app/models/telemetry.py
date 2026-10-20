from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from bson import ObjectId
from .user import PyObjectId

class TelemetriaBase(BaseModel):
    charge_point_id: str
    connector_id: Optional[int] = None
    transaction_id: Optional[int] = None
    tipo_medicion: str = Field(..., pattern="^(voltage|current|power|energy|temperature|soc|other)$")
    valor: float
    unidad: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class TelemetriaCreate(TelemetriaBase):
    pass

class TelemetriaResponse(TelemetriaBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
        arbitrary_types_allowed = True

class EventoOCPPBase(BaseModel):
    charge_point_id: str
    tipo_mensaje: str
    accion: str
    payload: dict
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class EventoOCPPResponse(EventoOCPPBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
        arbitrary_types_allowed = True

class HeartbeatBase(BaseModel):
    charge_point_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class StatusNotificationBase(BaseModel):
    charge_point_id: str
    connector_id: int
    estado: str
    error_code: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    info: Optional[str] = None
    vendor_id: Optional[str] = None
    vendor_error_code: Optional[str] = None

class MeterValueBase(BaseModel):
    charge_point_id: str
    connector_id: int
    transaction_id: int
    sampled_values: list
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class LogSistemaBase(BaseModel):
    nivel: str = Field(..., pattern="^(info|warning|error|debug)$")
    modulo: str
    mensaje: str
    datos: Optional[dict] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class LogSistemaResponse(LogSistemaBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
        arbitrary_types_allowed = True