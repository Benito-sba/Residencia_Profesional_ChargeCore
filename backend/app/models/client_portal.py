from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, EmailStr

from .user import PyObjectId


class ClientProfileResponse(BaseModel):
    id: PyObjectId = Field(alias="_id")
    nombre: str
    email: EmailStr
    telefono: Optional[str] = None
    activo: bool = True
    rol: str
    created_at: datetime
    total_cargas: int = 0

    class Config:
        populate_by_name = True
        json_encoders = {PyObjectId: str}
        arbitrary_types_allowed = True


class ClientVehicleCreate(BaseModel):
    marca: str = Field(..., min_length=1, max_length=50)
    modelo: str = Field(..., min_length=1, max_length=50)
    anio: int = Field(..., ge=2000, le=2030)
    placa: Optional[str] = None
    tipo_conector: str = Field(default="Type2", pattern="^(Type1|Type2|CCS|CHAdeMO|Tesla)$")
    capacidad_bateria_kwh: Optional[float] = None

    class Config:
        extra = "ignore"


class ClientVehicleUpdate(BaseModel):
    marca: Optional[str] = Field(default=None, min_length=1, max_length=50)
    modelo: Optional[str] = Field(default=None, min_length=1, max_length=50)
    anio: Optional[int] = Field(default=None, ge=2000, le=2030)
    placa: Optional[str] = None
    tipo_conector: Optional[str] = Field(default=None, pattern="^(Type1|Type2|CCS|CHAdeMO|Tesla)$")
    capacidad_bateria_kwh: Optional[float] = None

    class Config:
        extra = "ignore"


class ClientVehicleResponse(BaseModel):
    id: PyObjectId = Field(alias="_id")
    marca: str
    modelo: str
    anio: int
    placa: Optional[str] = None
    tipo_conector: str
    capacidad_bateria_kwh: Optional[float] = None
    activo: bool = True
    created_at: datetime

    class Config:
        populate_by_name = True
        json_encoders = {PyObjectId: str}
        arbitrary_types_allowed = True


class ClientSessionVehicle(BaseModel):
    marca: str
    modelo: str
    placa: Optional[str] = None


class ClientSessionResponse(BaseModel):
    id: str
    cargador: Optional[str] = None
    charge_point_id: Optional[str] = None
    connector_id: Optional[int] = None
    vehiculo: Optional[ClientSessionVehicle] = None
    fecha_inicio: Optional[datetime] = None
    fecha_fin: Optional[datetime] = None
    energia_consumida_kwh: Optional[float] = None
    duracion_minutos: Optional[int] = None
    estado: Optional[str] = None
    beneficio_aplicado: Optional[str] = None


class ClientActiveSessionVehicle(BaseModel):
    marca: str
    modelo: str
    placa: Optional[str] = None


class ClientActiveSessionResponse(BaseModel):
    id: str
    cargador: Optional[str] = None
    charge_point_id: Optional[str] = None
    connector_id: Optional[int] = None
    vehiculo: Optional[ClientActiveSessionVehicle] = None
    fecha_inicio: Optional[datetime] = None
    energia_consumida_kwh: Optional[float] = None
    potencia_actual_kw: Optional[float] = None
    ultima_actualizacion: Optional[datetime] = None
    duracion_minutos: Optional[int] = None
    estado: Optional[str] = None
    stop_requested: bool = False
    selected_reward_id: Optional[str] = None


class ClientActiveSessionEnvelope(BaseModel):
    active: bool
    session: Optional[ClientActiveSessionResponse] = None


class ClientStartChargeRequest(BaseModel):
    vehicle_id: str
    connector_id: int = Field(..., ge=1)


class ClientRewardSelectionRequest(BaseModel):
    reward_id: Optional[str] = None


class ClientRewardResponse(BaseModel):
    id: str
    nombre: Optional[str] = None
    empresa: Optional[str] = None
    descripcion: Optional[str] = None
    condiciones: Optional[str] = None
    fecha_otorgamiento: Optional[datetime] = None
    fecha_inicio: Optional[datetime] = None
    fecha_finalizacion: Optional[datetime] = None
    cargas_requeridas: Optional[int] = None
    cargas_acumuladas: Optional[int] = None
    usada: bool
    fecha_uso: Optional[datetime] = None
    estado: str


class ClientRewardsEnvelope(BaseModel):
    recompensas: list[ClientRewardResponse]
    total: int
