from collections.abc import Callable
from typing import Any


async def _count(collection, query: dict, session=None) -> int:
    return await collection.count_documents(query, session=session)


async def _find_ids(collection, query: dict, session=None) -> list:
    cursor = collection.find(query, {"_id": 1}, session=session)
    return [document["_id"] async for document in cursor]


async def _has_request_reference(
    requests,
    fields: tuple[str, ...],
    values: list,
    session=None,
) -> bool:
    if not values:
        return False
    for field in fields:
        if await _count(
            requests,
            {field: {"$in": values}},
            session=session,
        ):
            return True
    return False


async def has_vehicle_history(
    vehicle_id: Any,
    collection_getter: Callable[[str], Any],
    session=None,
) -> bool:
    vehicle_refs = [vehicle_id, str(vehicle_id)]
    if await _count(
        collection_getter("sesiones"),
        {"vehiculo_id": {"$in": vehicle_refs}},
        session=session,
    ):
        return True
    return await _has_request_reference(
        collection_getter("nfc_charge_requests"),
        ("vehicle_id", "vehiculo_id"),
        vehicle_refs,
        session=session,
    )


async def has_nfc_card_history(
    card: dict,
    collection_getter: Callable[[str], Any],
    session=None,
) -> bool:
    card_id = card["_id"]
    card_refs = [card_id, str(card_id)]
    sessions = collection_getter("sesiones")
    if await _count(
        sessions,
        {"nfc_card_id": {"$in": card_refs}},
        session=session,
    ):
        return True

    requests = collection_getter("nfc_charge_requests")
    if await _has_request_reference(
        requests,
        ("card_id", "nfc_card_id"),
        card_refs,
        session=session,
    ):
        return True
    uid = card.get("uid")
    return bool(
        uid
        and await _count(requests, {"uid": uid}, session=session)
    )


async def has_user_history(
    user: dict,
    collection_getter: Callable[[str], Any],
    session=None,
) -> bool:
    if user.get("total_cargas") or user.get("cargas"):
        return True

    user_id = user["_id"]
    sessions = collection_getter("sesiones")
    if await _count(sessions, {"usuario_id": user_id}, session=session):
        return True

    requests = collection_getter("nfc_charge_requests")
    if await _count(requests, {"usuario_id": user_id}, session=session):
        return True

    for collection_name, queries in (
        ("recompensas", ({"usuario_id": user_id},)),
        (
            "recompensa_progreso",
            ({"_id": user_id}, {"usuario_id": user_id}),
        ),
        (
            "company_reward_balances",
            ({"usuario_id": user_id},),
        ),
        (
            "company_reward_ledger",
            ({"usuario_id": user_id},),
        ),
        (
            "company_reward_redemptions",
            ({"usuario_id": user_id},),
        ),
    ):
        collection = collection_getter(collection_name)
        for query in queries:
            if await _count(collection, query, session=session):
                return True

    vehicle_ids = await _find_ids(
        collection_getter("vehiculos"),
        {"usuario_id": user_id},
        session=session,
    )
    card_cursor = collection_getter("nfc_cards").find(
        {"usuario_id": user_id},
        {"_id": 1, "uid": 1},
        session=session,
    )
    cards = [card async for card in card_cursor]
    card_ids = [card["_id"] for card in cards]

    vehicle_refs = vehicle_ids + [str(value) for value in vehicle_ids]
    card_refs = card_ids + [str(value) for value in card_ids]
    if vehicle_refs and await _count(
        sessions,
        {"vehiculo_id": {"$in": vehicle_refs}},
        session=session,
    ):
        return True
    if card_refs and await _count(
        sessions,
        {"nfc_card_id": {"$in": card_refs}},
        session=session,
    ):
        return True

    if await _has_request_reference(
        requests,
        ("vehicle_id", "vehiculo_id"),
        vehicle_refs,
        session=session,
    ):
        return True
    if await _has_request_reference(
        requests,
        ("card_id", "nfc_card_id"),
        card_refs,
        session=session,
    ):
        return True
    uids = [card["uid"] for card in cards if card.get("uid")]
    return await _has_request_reference(
        requests,
        ("uid",),
        uids,
        session=session,
    )
