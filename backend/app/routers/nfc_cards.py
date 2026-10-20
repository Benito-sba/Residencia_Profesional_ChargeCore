from datetime import datetime

from fastapi import APIRouter, HTTPException, status, Depends
from pymongo.errors import DuplicateKeyError
from app.database import get_collection
from app.auth import require_admin
from app.models.nfc_card import NFCCardCreate, NFCCardUpdate
from app.validation import parse_object_id

router = APIRouter(prefix="/api/nfc-cards", tags=["Tarjetas NFC"])


async def _require_active_client(user_id):
    user = await get_collection("usuarios").find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if user.get("rol") != "cliente" or not user.get("activo", True):
        raise HTTPException(
            status_code=409,
            detail="Las tarjetas NFC solo se pueden asignar a clientes activos",
        )


@router.get("/")
async def list_nfc_cards(current_user=Depends(require_admin)):
    cursor = get_collection("nfc_cards").find()
    cards = []
    async for c in cursor:
        c["_id"] = str(c["_id"])
        if c.get("usuario_id"):
            c["usuario_id"] = str(c["usuario_id"])
        cards.append(c)
    return cards

@router.get("/usuario/{user_id}")
async def get_user_cards(user_id: str, current_user=Depends(require_admin)):
    cursor = get_collection("nfc_cards").find(
        {"usuario_id": parse_object_id(user_id, "usuario")}
    )
    cards = []
    async for c in cursor:
        c["_id"] = str(c["_id"])
        cards.append(c)
    return cards

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_nfc_card(card: NFCCardCreate, current_user=Depends(require_admin)):
    await _require_active_client(card.usuario_id)

    existing = await get_collection("nfc_cards").find_one({"uid": card.uid})
    if existing:
        raise HTTPException(status_code=400, detail="UID ya registrado")

    try:
        result = await get_collection("nfc_cards").insert_one(card.model_dump())
    except DuplicateKeyError as error:
        raise HTTPException(status_code=409, detail="UID ya registrado") from error
    return {"id": str(result.inserted_id)}

@router.put("/{card_id}")
async def update_nfc_card(card_id: str, card: NFCCardUpdate, current_user=Depends(require_admin)):
    update_data = card.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=422, detail="No hay datos para actualizar")

    cards = get_collection("nfc_cards")
    object_id = parse_object_id(card_id, "tarjeta NFC")
    if await cards.find_one({"_id": object_id}) is None:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    if "usuario_id" in update_data:
        await _require_active_client(update_data["usuario_id"])

    result = await cards.update_one(
        {"_id": object_id},
        {"$set": update_data}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    return {"mensaje": "Tarjeta actualizada"}

@router.delete("/{card_id}")
async def delete_nfc_card(card_id: str, current_user=Depends(require_admin)):
    cards = get_collection("nfc_cards")
    object_id = parse_object_id(card_id, "tarjeta NFC")
    existing = await cards.find_one({"_id": object_id})
    if existing is None:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    if existing.get("estado") == "bloqueada":
        return {"mensaje": "La tarjeta ya estaba bloqueada; historial conservado"}

    await cards.update_one(
        {"_id": object_id},
        {"$set": {
            "estado": "bloqueada",
            "fecha_baja": datetime.utcnow(),
        }},
    )
    return {"mensaje": "Tarjeta bloqueada; historial conservado"}