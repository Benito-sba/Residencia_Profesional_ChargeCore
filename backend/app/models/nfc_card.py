from datetime import datetime
from typing import Annotated, Optional
from pydantic import BaseModel, Field
from pydantic import StringConstraints
from bson import ObjectId
from .user import PyObjectId

class NFCCardBase(BaseModel):
    uid: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=4, max_length=20),
    ]
    usuario_id: PyObjectId
    estado: str = Field(default="activa", pattern="^(activa|bloqueada|perdida)$")
    created_at: datetime = Field(default_factory=datetime.utcnow)

class NFCCardCreate(NFCCardBase):
    pass

class NFCCardUpdate(BaseModel):
    estado: Optional[str] = None
    usuario_id: Optional[PyObjectId] = None

class NFCCardResponse(NFCCardBase):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}
        arbitrary_types_allowed = True