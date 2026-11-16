from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, EmailStr, model_validator
from pydantic_core import core_schema
from bson import ObjectId

LEGACY_ROLES = frozenset({"admin", "operador", "consulta", "cliente"})
NEW_ROLES = frozenset({"superadmin", "dueno_empresa"})
ALL_ROLES_PATTERN = "^(admin|operador|consulta|cliente|superadmin|dueno_empresa)$"


class PyObjectId(ObjectId):
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        return core_schema.no_info_plain_validator_function(cls.validate)

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return v
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        return {"type": "string"}

class UsuarioBase(BaseModel):
    nombre: str = Field(..., min_length=2, max_length=100)
    email: EmailStr
    telefono: Optional[str] = None
    activo: bool = True
    rol: str = Field(..., pattern=ALL_ROLES_PATTERN)
    empresa_id: Optional[PyObjectId] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @model_validator(mode="after")
    def validate_company_role(self):
        if self.rol == "superadmin" and self.empresa_id is not None:
            raise ValueError("El superadministrador no puede pertenecer a una empresa")
        if self.rol == "dueno_empresa" and self.empresa_id is None:
            raise ValueError("El dueño de empresa debe tener una empresa asignada")
        return self


class UsuarioCreate(UsuarioBase):
    password: str = Field(..., min_length=6)


class UsuarioUpdate(BaseModel):
    nombre: Optional[str] = None
    email: Optional[EmailStr] = None
    telefono: Optional[str] = None
    activo: Optional[bool] = None
    rol: Optional[str] = Field(default=None, pattern=ALL_ROLES_PATTERN)
    empresa_id: Optional[PyObjectId] = None
    password: Optional[str] = Field(default=None, min_length=6)

    @model_validator(mode="after")
    def validate_company_role(self):
        if self.rol == "superadmin" and self.empresa_id is not None:
            raise ValueError("El superadministrador no puede pertenecer a una empresa")
        if self.rol == "dueno_empresa" and self.empresa_id is None:
            raise ValueError("El dueño de empresa debe tener una empresa asignada")
        return self


class UsuarioResponse(UsuarioBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    total_cargas: int = 0

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
        arbitrary_types_allowed = True