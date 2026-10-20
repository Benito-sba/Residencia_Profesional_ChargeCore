from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException


def parse_object_id(
    value: str,
    entity_name: str,
    *,
    status_code: int = 422,
) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(
            status_code=status_code,
            detail=f"El identificador de {entity_name} no es válido",
        )
