from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictRewardModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CommercialBenefitRequirements(StrictRewardModel):
    min_completed_sessions: int = Field(default=0, ge=0)
    min_active_clients: int = Field(default=0, ge=0)
    min_active_chargers: int = Field(default=0, ge=0)


class CommercialBenefitUsageConditions(StrictRewardModel):
    min_days_after_assignment: int = Field(default=0, ge=0)
    max_days_after_assignment: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_usage_window(self):
        if (
            self.max_days_after_assignment is not None
            and self.min_days_after_assignment > self.max_days_after_assignment
        ):
            raise ValueError("La ventana de uso no es válida")
        return self


class CommercialBenefitCreate(StrictRewardModel):
    nombre: str = Field(..., min_length=1, max_length=150)
    categoria: Literal[
        "promociones_dias_servicio",
        "creditos_descuentos",
        "crecimiento_permanencia",
        "reconocimientos_especiales",
    ]
    tipo: Literal["dias_servicio", "credito", "descuento", "reconocimiento"]
    descripcion: str = Field(..., min_length=1, max_length=1000)
    condiciones: str = Field(default="", max_length=2000)
    requisitos: CommercialBenefitRequirements = Field(
        default_factory=CommercialBenefitRequirements
    )
    condiciones_uso: CommercialBenefitUsageConditions = Field(
        default_factory=CommercialBenefitUsageConditions
    )
    valor: float | None = Field(default=None, gt=0)
    fecha_inicio: datetime
    fecha_finalizacion: datetime
    max_assignments_per_company: int | None = Field(default=None, ge=1)
    activa: bool = True

    @model_validator(mode="after")
    def validate_dates(self):
        if self.fecha_inicio > self.fecha_finalizacion:
            raise ValueError("La fecha de inicio no puede superar la fecha final")
        if self.tipo == "reconocimiento" and self.valor is not None:
            raise ValueError("Un reconocimiento no admite valor numérico")
        if self.tipo != "reconocimiento" and self.valor is None:
            raise ValueError("Este tipo de beneficio requiere un valor")
        return self


class CommercialBenefitUpdate(StrictRewardModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=150)
    categoria: Literal[
        "promociones_dias_servicio",
        "creditos_descuentos",
        "crecimiento_permanencia",
        "reconocimientos_especiales",
    ] | None = None
    tipo: Literal["dias_servicio", "credito", "descuento", "reconocimiento"] | None = None
    descripcion: str | None = Field(default=None, min_length=1, max_length=1000)
    condiciones: str | None = Field(default=None, max_length=2000)
    requisitos: CommercialBenefitRequirements | None = None
    condiciones_uso: CommercialBenefitUsageConditions | None = None
    valor: float | None = Field(default=None, gt=0)
    fecha_inicio: datetime | None = None
    fecha_finalizacion: datetime | None = None
    max_assignments_per_company: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_dates(self):
        if (
            self.fecha_inicio is not None
            and self.fecha_finalizacion is not None
            and self.fecha_inicio > self.fecha_finalizacion
        ):
            raise ValueError("La fecha de inicio no puede superar la fecha final")
        return self


class CommercialBenefitState(StrictRewardModel):
    activa: bool


class CommercialBenefitAssignmentRequest(StrictRewardModel):
    empresa_ids: list[str] = Field(..., min_length=1, max_length=100)
    assignment_request_id: UUID | None = None


class CompanyRewardCreate(StrictRewardModel):
    nombre: str = Field(..., min_length=1, max_length=150)
    descripcion: str = Field(..., min_length=1, max_length=1000)
    condiciones: str = Field(default="", max_length=2000)
    puntos_requeridos: int = Field(..., ge=1, le=1_000_000)
    activa: bool = True
    fecha_finalizacion: datetime | None = None


class CompanyRewardUpdate(StrictRewardModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=150)
    descripcion: str | None = Field(default=None, min_length=1, max_length=1000)
    condiciones: str | None = Field(default=None, max_length=2000)
    puntos_requeridos: int | None = Field(default=None, ge=1, le=1_000_000)
    fecha_finalizacion: datetime | None = None


class CompanyRewardState(StrictRewardModel):
    activa: bool


class CompanyRewardRule(StrictRewardModel):
    puntos_por_carga: int = Field(..., ge=1, le=1_000_000)
    energia_minima_kwh: float = Field(default=0, ge=0, le=1_000_000)
    activa: bool = True


class CompanyRedemptionRequest(StrictRewardModel):
    recompensa_id: str = Field(..., min_length=1, max_length=64)


class CompanyRedemptionDecision(StrictRewardModel):
    decision: Literal["aprobar", "rechazar"]
    nota: str | None = Field(default=None, max_length=500)
