import unicodedata
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .user import PyObjectId


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_company_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", " ".join(name.split()))
    return normalized.casefold()


class Empresa(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    nombre: str = Field(..., min_length=1, max_length=150)
    nombre_normalizado: str
    contacto_nombre: str | None = Field(default=None, max_length=150)
    contacto_email: EmailStr | None = Field(default=None, max_length=254)
    contacto_telefono: str | None = Field(default=None, max_length=40)
    activo: bool = True
    estado: Literal["activa", "suspendida", "inactiva"] = "activa"
    created_at: datetime = Field(default_factory=_utcnow_naive)
    updated_at: datetime = Field(default_factory=_utcnow_naive)

    @field_validator("nombre", mode="before")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("contacto_nombre", "contacto_email", "contacto_telefono", mode="before")
    @classmethod
    def normalize_optional_contact(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="before")
    @classmethod
    def populate_normalized_name(cls, value):
        if isinstance(value, dict):
            document = value.copy()
            name = document.get("nombre")
            if isinstance(name, str) and not document.get("nombre_normalizado"):
                document["nombre_normalizado"] = normalize_company_name(name)
            if "estado" not in document:
                document["estado"] = (
                    "activa" if document.get("activo", True) else "inactiva"
                )
            return document
        return value

    @model_validator(mode="after")
    def validate_normalized_name(self):
        if self.nombre_normalizado != normalize_company_name(self.nombre):
            raise ValueError("El nombre normalizado no coincide con el nombre")
        if self.activo != (self.estado == "activa"):
            raise ValueError("activo y estado deben representar el mismo estado")
        return self

