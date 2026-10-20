from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, model_validator
from bson import ObjectId
from .user import PyObjectId

class RecompensaBase(BaseModel):
    usuario_id: PyObjectId
    tipo: str = Field(..., pattern="^(carga_gratuita|beneficio_especial)$")
    descripcion: str
    cargas_requeridas: int
    cargas_acumuladas: int = 0
    usado: bool = False
    fecha_otorgamiento: datetime = Field(default_factory=datetime.utcnow)
    fecha_uso: Optional[datetime] = None
    sesion_id_uso: Optional[PyObjectId] = None
    recompensa_catalogo_id: Optional[PyObjectId] = None
    recompensa_regla_id: Optional[PyObjectId] = None
    snapshot_nombre: Optional[str] = None
    snapshot_empresa: Optional[str] = None
    snapshot_descripcion: Optional[str] = None
    snapshot_condiciones: Optional[str] = None
    snapshot_cargas_requeridas: Optional[int] = Field(default=None, ge=1)
    snapshot_fecha_inicio: Optional[datetime] = None
    snapshot_fecha_finalizacion: Optional[datetime] = None

class RecompensaCreate(RecompensaBase):
    pass

class RecompensaResponse(RecompensaBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
        arbitrary_types_allowed = True


class RecompensaCatalogoBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=150)
    empresa: str = Field(..., min_length=1, max_length=150)
    descripcion: str = Field(..., min_length=1, max_length=500)
    condiciones: str = Field(default="", max_length=500)
    fecha_inicio: datetime
    fecha_finalizacion: datetime
    activa: bool = True
    fecha_creacion: datetime = Field(default_factory=datetime.utcnow)
    fecha_actualizacion: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.fecha_inicio > self.fecha_finalizacion:
            raise ValueError("La fecha de inicio no puede ser posterior a la fecha de finalización")
        return self


class RecompensaCatalogoCreate(RecompensaCatalogoBase):
    pass


class RecompensaCatalogoUpdate(BaseModel):
    nombre: Optional[str] = Field(default=None, min_length=1, max_length=150)
    empresa: Optional[str] = Field(default=None, min_length=1, max_length=150)
    descripcion: Optional[str] = Field(default=None, min_length=1, max_length=500)
    condiciones: Optional[str] = Field(default=None, max_length=500)
    fecha_inicio: Optional[datetime] = None
    fecha_finalizacion: Optional[datetime] = None
    activa: Optional[bool] = None


class RecompensaCatalogoResponse(RecompensaCatalogoBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
        arbitrary_types_allowed = True


class ReglaRecompensaBase(BaseModel):
    cargas_requeridas: int = Field(..., ge=1)
    recompensa_catalogo_id: PyObjectId
    activa: bool = True
    fecha_creacion: datetime = Field(default_factory=datetime.utcnow)
    fecha_actualizacion: datetime = Field(default_factory=datetime.utcnow)


class ReglaRecompensaCreate(ReglaRecompensaBase):
    pass


class ReglaRecompensaUpdate(BaseModel):
    cargas_requeridas: Optional[int] = Field(default=None, ge=1)
    recompensa_catalogo_id: Optional[PyObjectId] = None
    activa: Optional[bool] = None


class ReglaRecompensaEstado(BaseModel):
    activa: bool


class ReglaRecompensaResponse(ReglaRecompensaBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
        arbitrary_types_allowed = True