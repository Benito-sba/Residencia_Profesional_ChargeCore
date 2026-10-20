from typing import Optional

from pydantic import BaseModel, Field


class RemoteStartRequest(BaseModel):
    id_tag: str = Field(..., alias="idTag", min_length=1, max_length=20)
    connector_id: Optional[int] = Field(default=None, alias="connectorId", ge=0)

    class Config:
        populate_by_name = True


class RemoteStopRequest(BaseModel):
    transaction_id: int = Field(..., alias="transactionId", ge=1)

    class Config:
        populate_by_name = True


class ResetRequest(BaseModel):
    type: str = Field(default="Soft", pattern="^(Soft|Hard)$")


class ChangeAvailabilityRequest(BaseModel):
    connector_id: int = Field(..., alias="connectorId", ge=0)
    type: str = Field(default="Operative", pattern="^(Operative|Inoperative)$")

    class Config:
        populate_by_name = True