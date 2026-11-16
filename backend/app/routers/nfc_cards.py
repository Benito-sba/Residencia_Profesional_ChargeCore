from datetime import datetime

from fastapi import APIRouter, HTTPException, status, Depends
from pymongo.errors import DuplicateKeyError, PyMongoError
from app.database import get_collection, get_database
from app.auth import company_scoped_query, require_company_admin_access
from app.models.nfc_card import NFCCardCreate, NFCCardUpdate
from app.services.safe_deletion import has_nfc_card_history
from app.validation import parse_object_id

router = APIRouter(prefix="/api/nfc-cards", tags=["Tarjetas NFC"])


async def _require_active_client(user_id, current_user):
    user_query = company_scoped_query(current_user, {
        "_id": user_id,
        "rol": "cliente",
        "activo": {"$ne": False},
    })
    user = await get_collection("usuarios").find_one(user_query)
    if user is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    company_id = user.get("empresa_id")
    if company_id is None:
        raise HTTPException(
            status_code=409,
            detail="No se pueden asignar tarjetas a un cliente sin empresa",
        )
    company = await get_collection("empresas").find_one({
        "_id": company_id,
        "activo": True,
    })
    if company is None:
        raise HTTPException(status_code=409, detail="La empresa del cliente no está disponible")
    return user


@router.get("/")
async def list_nfc_cards(current_user=Depends(require_company_admin_access)):
    cursor = get_collection("nfc_cards").find(
        company_scoped_query(current_user, {"archivado": {"$ne": True}})
    )
    cards = []
    async for c in cursor:
        c["_id"] = str(c["_id"])
        if c.get("usuario_id"):
            c["usuario_id"] = str(c["usuario_id"])
        if c.get("empresa_id"):
            c["empresa_id"] = str(c["empresa_id"])
        cards.append(c)
    return cards

@router.get("/usuario/{user_id}")
async def get_user_cards(
    user_id: str,
    current_user=Depends(require_company_admin_access),
):
    target_user_id = parse_object_id(user_id, "usuario")
    await _require_active_client(target_user_id, current_user)
    cursor = get_collection("nfc_cards").find(
        company_scoped_query(
            current_user,
            {
                "usuario_id": target_user_id,
                "archivado": {"$ne": True},
            },
        )
    )
    cards = []
    async for c in cursor:
        c["_id"] = str(c["_id"])
        if c.get("usuario_id"):
            c["usuario_id"] = str(c["usuario_id"])
        if c.get("empresa_id"):
            c["empresa_id"] = str(c["empresa_id"])
        cards.append(c)
    return cards

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_nfc_card(
    card: NFCCardCreate,
    current_user=Depends(require_company_admin_access),
):
    user = await _require_active_client(card.usuario_id, current_user)

    existing = await get_collection("nfc_cards").find_one({"uid": card.uid})
    if existing:
        raise HTTPException(status_code=400, detail="UID ya registrado")

    try:
        card_data = card.model_dump()
        card_data["empresa_id"] = user["empresa_id"]
        result = await get_collection("nfc_cards").insert_one(card_data)
    except DuplicateKeyError as error:
        raise HTTPException(status_code=409, detail="UID ya registrado") from error
    return {"id": str(result.inserted_id)}

@router.put("/{card_id}")
async def update_nfc_card(
    card_id: str,
    card: NFCCardUpdate,
    current_user=Depends(require_company_admin_access),
):
    update_data = card.model_dump(exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=422, detail="No hay datos para actualizar")
    if update_data.get("estado") == "bloqueada":
        update_data.setdefault("fecha_baja", datetime.utcnow())

    cards = get_collection("nfc_cards")
    object_id = parse_object_id(card_id, "tarjeta NFC")
    card_filter = company_scoped_query(
        current_user,
        {"_id": object_id, "archivado": {"$ne": True}},
    )
    if await cards.find_one(card_filter) is None:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")

    if "usuario_id" in update_data:
        user = await _require_active_client(update_data["usuario_id"], current_user)
        update_data["empresa_id"] = user["empresa_id"]

    result = await cards.update_one(
        card_filter,
        {"$set": update_data}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Tarjeta no encontrada")
    return {"mensaje": "Tarjeta actualizada"}

@router.delete("/{card_id}")
async def delete_nfc_card(
    card_id: str,
    current_user=Depends(require_company_admin_access),
):
    cards = get_collection("nfc_cards")
    object_id = parse_object_id(card_id, "tarjeta NFC")
    card_filter = company_scoped_query(current_user, {"_id": object_id})
    try:
        async with await get_database().client.start_session() as session:
            async with session.start_transaction():
                existing = await cards.find_one(card_filter, session=session)
                if existing is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Tarjeta no encontrada",
                    )
                if existing.get("archivado"):
                    return {"mensaje": "La tarjeta ya estaba archivada"}

                if await has_nfc_card_history(
                    existing,
                    get_collection,
                    session=session,
                ):
                    await cards.update_one(
                        card_filter,
                        {"$set": {
                            "estado": "bloqueada",
                            "archivado": True,
                            "fecha_baja": datetime.utcnow(),
                        }},
                        session=session,
                    )
                    return {
                        "mensaje": (
                            "Tarjeta archivada y bloqueada; "
                            "historial conservado para reportes"
                        )
                    }
                result = await cards.delete_one(card_filter, session=session)
                if result.deleted_count != 1:
                    raise HTTPException(
                        status_code=404,
                        detail="Tarjeta no encontrada",
                    )
                return {"mensaje": "Tarjeta eliminada permanentemente"}
    except PyMongoError as exc:
        if exc.has_error_label("TransientTransactionError"):
            raise HTTPException(
                status_code=409,
                detail="Hay otra eliminación en curso; vuelve a intentarlo",
            ) from exc
        if exc.has_error_label("UnknownTransactionCommitResult"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "No se pudo confirmar con certeza la eliminación; "
                    "verifica el registro antes de repetirla"
                ),
            ) from exc
        raise