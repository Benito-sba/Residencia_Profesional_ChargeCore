from app.services.ocpp_protocol_settings import OCPP_16_SUBPROTOCOLS


OCPP_PROTOCOL_ADAPTERS = {
    "ocpp2.1": "v21",
    "ocpp2.0.1": "v201",
    "ocpp1.6": "v16",
    "ocpp1.6j": "v16",
}

IMPLEMENTED_OCPP_ADAPTERS = frozenset({"v16", "v201", "v21"})
OCPP_SUBPROTOCOL_PRIORITY = ("ocpp1.6", "ocpp2.0.1", "ocpp2.1")
OCPP_SUBPROTOCOLS = tuple(OCPP_PROTOCOL_ADAPTERS)


def select_ocpp_subprotocol(
    requested_protocols: list[str],
    *,
    enabled_protocols: list[str] | tuple[str, ...] = (
        "ocpp1.6",
        "ocpp2.0.1",
        "ocpp2.1",
    ),
    automatic_detection: bool = True,
    preferred_protocol: str | None = "ocpp1.6",
) -> str | None:
    enabled = set(enabled_protocols)
    if automatic_detection:
        protocol_order = OCPP_SUBPROTOCOL_PRIORITY
    else:
        if preferred_protocol is None or preferred_protocol not in enabled:
            return None
        protocol_order = (preferred_protocol,)

    announced = set(requested_protocols)
    for protocol in protocol_order:
        if protocol not in enabled:
            continue
        candidates = (
            OCPP_16_SUBPROTOCOLS if protocol == "ocpp1.6" else (protocol,)
        )
        for candidate in candidates:
            if candidate in announced:
                adapter = OCPP_PROTOCOL_ADAPTERS.get(candidate)
                if adapter in IMPLEMENTED_OCPP_ADAPTERS:
                    return candidate
    return None
