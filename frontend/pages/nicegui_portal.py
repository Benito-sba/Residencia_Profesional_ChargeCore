import json
from uuid import uuid4
from urllib.parse import parse_qs, unquote, urlparse

from nicegui import app, ui

from components.navigation import PAGE_ROUTES
from pages.client_page_utils import (
    client_error,
    page_container,
    render_client_shell,
    show_error,
)
from utils.api_client import api_get, api_post


def parse_charger_qr(value):
    if not isinstance(value, str):
        return None
    content = value.strip()
    if not content or len(content) > 2048:
        return None

    charger_id = None
    if content.startswith("{"):
        try:
            data = json.loads(content)
        except ValueError:
            return None
        if isinstance(data, dict):
            for key, candidate in data.items():
                normalized_key = key.lower().replace("_", "").replace("-", "")
                if normalized_key in {"chargepointid", "chargerid", "charger"}:
                    charger_id = candidate
                    break
    elif content.lower().startswith(("http://", "https://")):
        parsed = urlparse(content)
        query = parse_qs(parsed.query)
        for key, values in query.items():
            normalized_key = key.lower().replace("_", "").replace("-", "")
            if normalized_key in {"chargepointid", "chargerid", "charger"} and values:
                charger_id = values[0]
                break
        if charger_id is None:
            path_parts = [
                unquote(part)
                for part in parsed.path.split("/")
                if part
            ]
            if (
                len(path_parts) >= 2
                and path_parts[-2].lower() in {"charger", "chargers"}
            ):
                charger_id = path_parts[-1]
    elif content.lower().startswith("chargecore://charger/"):
        parsed = urlparse(content)
        charger_id = unquote(parsed.path.strip("/"))
    elif content.lower().startswith("chargecore:charger:"):
        charger_id = content.split(":", 2)[2]
    else:
        charger_id = content

    if not isinstance(charger_id, str):
        return None
    charger_id = charger_id.strip()
    if (
        not charger_id
        or len(charger_id) > 50
        or any(ord(character) < 32 for character in charger_id)
    ):
        return None
    return charger_id


def _qr_scanner_script(scanner_id, video_id):
    return """
        (() => {
            const key = __SCANNER_KEY__;
            const video = document.getElementById(__VIDEO_ID__);
            const state = window[key] = {
                running: true,
                value: null,
                error: null,
                stream: null
            };
            const stop = () => {
                state.running = false;
                if (state.stream) {
                    state.stream.getTracks().forEach(track => track.stop());
                    state.stream = null;
                }
                if (video) video.srcObject = null;
            };
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                state.error = 'El navegador no permite usar la cámara. Usa una conexión segura o ingresa el ID del cargador.';
                return true;
            }
            if (!('BarcodeDetector' in window)) {
                state.error = 'Este navegador no incluye lector QR. Prueba con Chrome o Edge, o ingresa el ID del cargador.';
                return true;
            }
            navigator.mediaDevices.getUserMedia({
                video: {facingMode: {ideal: 'environment'}},
                audio: false
            }).then(async stream => {
                if (!state.running) {
                    stream.getTracks().forEach(track => track.stop());
                    return;
                }
                state.stream = stream;
                video.srcObject = stream;
                try {
                    await video.play();
                    const detector = new BarcodeDetector({formats: ['qr_code']});
                    const scanFrame = async () => {
                        if (!state.running) return;
                        if (video.readyState < HTMLMediaElement.HAVE_ENOUGH_DATA) {
                            requestAnimationFrame(scanFrame);
                            return;
                        }
                        try {
                            const codes = await detector.detect(video);
                            if (codes.length && codes[0].rawValue) {
                                state.value = codes[0].rawValue;
                                stop();
                                return;
                            }
                        } catch (error) {
                            state.error = 'No fue posible leer el QR con la cámara.';
                            stop();
                            return;
                        }
                        requestAnimationFrame(scanFrame);
                    };
                    scanFrame();
                } catch (error) {
                    state.error = 'No fue posible iniciar la cámara. Verifica los permisos del navegador.';
                    stop();
                }
            }).catch(error => {
                state.error = error && error.name === 'NotAllowedError'
                    ? 'Permite el acceso a la cámara para escanear el QR.'
                    : 'No fue posible abrir la cámara. Ingresa el ID del cargador manualmente.';
                stop();
            });
            return true;
        })()
    """.replace(
        "__SCANNER_KEY__", json.dumps(scanner_id)
    ).replace("__VIDEO_ID__", json.dumps(video_id))


def _qr_scanner_state_script(scanner_id):
    return """
        (() => {
            const state = window[__SCANNER_KEY__];
            if (!state) return null;
            const result = {value: state.value, error: state.error};
            state.value = null;
            state.error = null;
            return result;
        })()
    """.replace("__SCANNER_KEY__", json.dumps(scanner_id))


def _qr_scanner_stop_script(scanner_id, video_id):
    return """
        (() => {
            const state = window[__SCANNER_KEY__];
            if (state) {
                state.running = false;
                if (state.stream) {
                    state.stream.getTracks().forEach(track => track.stop());
                    state.stream = null;
                }
            }
            const video = document.getElementById(__VIDEO_ID__);
            if (video) video.srcObject = null;
            return true;
        })()
    """.replace(
        "__SCANNER_KEY__", json.dumps(scanner_id)
    ).replace("__VIDEO_ID__", json.dumps(video_id))


def open_qr_charge_dialog(token, api_base_url):
    scanner_id = f"chargecoreQrScanner_{uuid4().hex}"
    video_id = f"chargecoreQrVideo_{uuid4().hex}"
    busy = {"value": False}

    with ui.dialog() as dialog, ui.card().classes(
        "chargecore-card w-full max-w-lg gap-4 p-5"
    ):
        ui.label("Escanear QR del cargador").classes(
            "text-xl font-bold text-white"
        )
        ui.label(
            "Apunta la cámara al código QR del cargador. Después podrás elegir "
            "tu vehículo y conector antes de confirmar."
        ).classes("text-sm leading-6 text-slate-400")
        ui.element("video").props(
            f"id={video_id} autoplay muted playsinline"
        ).classes("w-full overflow-hidden rounded-xl bg-black")
        scanner_status = ui.label("Preparando cámara…").classes(
            "text-sm text-slate-300"
        )
        charger_code = ui.input(
            "ID del cargador (opcional)",
            placeholder="Ejemplo: CARGADOR-01",
        ).props("outlined maxlength=50").classes("w-full")
        with ui.row().classes("w-full flex-wrap justify-end gap-2"):
            retry_button = ui.button(
                "Volver a escanear",
                icon="qr_code_scanner",
            ).props("flat no-caps").classes("text-orange-300")
            continue_button = ui.button(
                "Continuar",
                icon="bolt",
            ).props("no-caps").classes(
                "chargecore-primary bg-[#ff500a] text-white"
            )
            cancel_button = ui.button("Cancelar").props(
                "flat no-caps"
            ).classes("text-slate-300")

    async def stop_camera():
        await ui.run_javascript(
            _qr_scanner_stop_script(scanner_id, video_id)
        )

    async def start_scanning():
        scanner_status.set_text("Solicitando acceso a la cámara…")
        await ui.run_javascript(_qr_scanner_script(scanner_id, video_id))

    async def submit_charge_request(raw_value):
        if busy["value"]:
            return
        charge_point_id = parse_charger_qr(raw_value)
        if charge_point_id is None:
            scanner_status.set_text(
                "No se reconoció un ID de cargador válido. Escanea de nuevo "
                "o escribe el ID manualmente."
            )
            ui.notify("El QR no contiene un ID de cargador válido.", type="warning")
            return

        busy["value"] = True
        continue_button.disable()
        retry_button.disable()
        await stop_camera()
        result = api_post(
            "/client/me/qr-charge-requests",
            json={"charge_point_id": charge_point_id},
            token=token,
            base_url=api_base_url,
        )
        if isinstance(result, dict) and "error" in result:
            scanner_status.set_text(client_error(
                result,
                "No fue posible preparar la carga con este QR.",
            ))
            ui.notify(
                client_error(result, "No fue posible preparar la carga con este QR."),
                type="negative",
            )
            busy["value"] = False
            continue_button.enable()
            retry_button.enable()
            return
        if (
            not isinstance(result, dict)
            or not result.get("id")
            or result.get("status") != "awaiting_vehicle"
        ):
            scanner_status.set_text(
                "El servidor devolvió una solicitud de carga inesperada."
            )
            ui.notify(
                "No se pudo validar la solicitud de carga recibida.",
                type="negative",
            )
            busy["value"] = False
            continue_button.enable()
            retry_button.enable()
            return

        scanner_timer.cancel()
        dialog.close()
        ui.notify(
            "Cargador identificado. Elige tu vehículo y conector para continuar.",
            type="positive",
        )
        ui.navigate.to(PAGE_ROUTES["mis_cargas"])

    async def poll_scanner():
        state = await ui.run_javascript(
            _qr_scanner_state_script(scanner_id)
        )
        if not isinstance(state, dict):
            return
        if state.get("error"):
            scanner_status.set_text(state["error"])
        if state.get("value"):
            await submit_charge_request(state["value"])

    async def continue_manually():
        await submit_charge_request(charger_code.value)

    async def close_dialog():
        scanner_timer.cancel()
        await stop_camera()
        dialog.close()

    retry_button.on("click", start_scanning)
    continue_button.on("click", continue_manually)
    cancel_button.on("click", close_dialog)
    dialog.props("persistent")
    dialog.open()
    scanner_timer = ui.timer(0.5, poll_scanner, immediate=False)
    ui.timer(0.1, start_scanning, once=True)


def render_page(token: str, api_base_url: str):
    if not render_client_shell("inicio", token, api_base_url):
        return

    profile = api_get("/client/me", token=token, base_url=api_base_url)
    vehicles = api_get(
        "/client/me/vehicles",
        token=token,
        base_url=api_base_url,
    )
    sessions = api_get(
        "/client/me/sessions",
        token=token,
        base_url=api_base_url,
    )
    rewards = api_get(
        "/client/me/rewards",
        token=token,
        base_url=api_base_url,
    )

    with page_container():
        for result, fallback in (
            (profile, "No fue posible cargar tu perfil."),
            (vehicles, "No fue posible cargar tus vehículos."),
            (sessions, "No fue posible cargar tus cargas."),
            (rewards, "No fue posible cargar tus recompensas."),
        ):
            if isinstance(result, dict) and "error" in result:
                show_error(client_error(result, fallback))
                return

        if not isinstance(profile, dict) or not isinstance(vehicles, list):
            show_error("El servidor devolvió información del perfil con un formato inesperado.")
            return
        if not isinstance(sessions, list):
            show_error("El historial de cargas tiene un formato inesperado.")
            return
        if not isinstance(rewards, dict) or not isinstance(
            rewards.get("recompensas"), list
        ):
            show_error("La respuesta de recompensas tiene un formato inesperado.")
            return

        user = app.storage.user.get("user") or {}
        client_name = profile.get("nombre") or user.get("nombre") or "Cliente"
        total_charges = profile.get("total_cargas")
        if not isinstance(total_charges, int) or isinstance(total_charges, bool):
            total_charges = sum(
                str(session.get("estado") or "").lower()
                in {"finalizada", "finalizado", "completed"}
                for session in sessions
                if isinstance(session, dict)
            )
        total_energy = sum(
            session["energia_consumida_kwh"]
            for session in sessions
            if isinstance(session, dict)
            and isinstance(session.get("energia_consumida_kwh"), (int, float))
            and not isinstance(session.get("energia_consumida_kwh"), bool)
        )
        rewards_list = rewards["recompensas"]
        available_rewards = sum(
            reward.get("estado") == "Disponible"
            for reward in rewards_list
            if isinstance(reward, dict)
        )

        with ui.row().classes("w-full items-center justify-between gap-4"):
            with ui.column().classes("gap-1"):
                ui.label(f"Hola, {client_name}").classes(
                    "text-3xl font-bold text-white"
                )
                ui.label("Bienvenido a tu portal de carga ChargeCore.").classes(
                    "text-sm text-slate-400"
                )

        with ui.element("div").classes(
            "grid w-full grid-cols-1 gap-4 lg:grid-cols-[0.95fr_0.95fr_1.15fr]"
        ):
            with ui.element("div").classes(
                "relative flex min-h-52 flex-col justify-between overflow-hidden "
                "rounded-2xl p-6 shadow-xl"
            ).style(
                "background: linear-gradient(120deg, #f2541b 0%, #c9360c 62%, #261c19 100%);"
            ):
                ui.element("div").style(
                    "position:absolute; width:180px; height:180px; top:-55px; "
                    "right:-45px; border-radius:9999px; background:#ffffff22;"
                )
                with ui.row().classes(
                    "relative z-10 w-full items-center justify-between"
                ):
                    with ui.row().classes("items-center gap-3"):
                        ui.icon("bolt").classes("text-2xl text-orange-200")
                        ui.label("ChargeCore").classes(
                            "text-lg font-bold text-white"
                        )
                    ui.icon("contactless").classes("text-2xl text-white")
                with ui.column().classes("relative z-10 gap-3"):
                    ui.element("div").classes(
                        "h-8 w-12 rounded-lg border border-amber-200/50 "
                        "bg-gradient-to-br from-amber-200 to-yellow-500"
                    )
                    ui.label("TARJETA NFC").classes(
                        "text-xs font-semibold tracking-widest text-orange-100"
                    )
                with ui.row().classes(
                    "relative z-10 w-full items-end justify-between"
                ):
                    with ui.column().classes("gap-0"):
                        ui.label("TITULAR").classes(
                            "text-[10px] font-medium tracking-wide text-orange-100"
                        )
                        ui.label(client_name.upper()).classes(
                            "font-bold text-white"
                        )
                    ui.label("Acércala al lector").classes(
                        "rounded-full bg-black/20 px-3 py-1 text-xs text-white"
                    )

            with ui.card().classes(
                "chargecore-card flex min-h-52 w-full flex-col justify-center "
                "gap-3 p-5"
            ):
                ui.icon("qr_code_scanner").classes(
                    "text-3xl text-orange-400"
                )
                ui.label("¿Sin tu tarjeta física?").classes(
                    "text-lg font-bold text-white"
                )
                ui.label(
                    "Escanea el QR del cargador para iniciar desde tu cuenta."
                ).classes("text-sm leading-6 text-slate-400")
                ui.button(
                    "Escanear QR",
                    icon="qr_code_scanner",
                    on_click=lambda: open_qr_charge_dialog(
                        token,
                        api_base_url,
                    ),
                ).props("no-caps").classes(
                    "chargecore-primary bg-[#ff500a] text-white"
                )

            with ui.card().classes(
                "chargecore-card flex w-full flex-col justify-center gap-3 p-6"
            ):
                ui.label("¿Listo para cargar?").classes(
                    "text-lg font-bold text-white"
                )
                ui.label(
                    "Acerca tu tarjeta NFC al lector del cargador, elige tu "
                    "vehículo y conector, y confirma para comenzar."
                ).classes("text-sm leading-6 text-slate-400")
                with ui.row().classes("flex-wrap gap-3"):
                    ui.button(
                        "Iniciar carga",
                        icon="bolt",
                        on_click=lambda: ui.navigate.to(
                            PAGE_ROUTES["mis_cargas"]
                        ),
                    ).props("no-caps").classes(
                        "chargecore-primary bg-[#ff500a] text-white"
                    )
                    ui.button(
                        "Mis vehículos",
                        icon="directions_car",
                        on_click=lambda: ui.navigate.to(
                            PAGE_ROUTES["mis_vehiculos"]
                        ),
                    ).props("outline no-caps").classes("text-white")

        with ui.row().classes("items-center gap-2"):
            ui.element("span").classes(
                "h-5 w-1 rounded-full bg-[#ff500a]"
            )
            ui.label("Tu resumen").classes("text-base font-bold text-white")

        with ui.element("div").classes(
            "grid w-full grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
        ):
            stats = (
                (
                    "VEHÍCULOS ACTIVOS",
                    str(len(vehicles)),
                    "Asociados a tu cuenta",
                    "directions_car",
                ),
                (
                    "CARGAS REALIZADAS",
                    str(total_charges),
                    "Historial completo",
                    "bolt",
                ),
                (
                    "ENERGÍA RECIBIDA",
                    f"{total_energy:g} kWh",
                    "En todas tus cargas",
                    "battery_charging_full",
                ),
                (
                    "RECOMPENSAS DISPONIBLES",
                    str(available_rewards),
                    "Listas para consultar",
                    "redeem",
                ),
            )
            for index, (label, value, caption, icon) in enumerate(stats):
                card_classes = (
                    "w-full gap-3 border border-orange-400 bg-[#f2541b] p-5"
                    if index == 0
                    else "chargecore-card w-full gap-3 p-5"
                )
                with ui.card().classes(card_classes):
                    ui.icon(icon).classes(
                        "rounded-xl bg-black/10 p-2 text-2xl text-orange-100"
                    )
                    ui.label(label).classes(
                        "text-xs font-bold tracking-wide text-white/75"
                    )
                    ui.label(value).classes("text-3xl font-bold text-white")
                    ui.label(caption).classes("text-xs text-white/70")

        with ui.card().classes("chargecore-card w-full gap-3 p-5"):
            with ui.row().classes(
                "w-full flex-wrap items-center justify-between gap-2"
            ):
                ui.label("Tus recompensas").classes(
                    "text-base font-bold text-white"
                )
                ui.label(f"{available_rewards} disponibles").classes(
                    "text-sm text-slate-400"
                )
            ui.label(
                "Consulta tus beneficios, condiciones y vigencia en la sección "
                "Mis recompensas."
            ).classes("text-sm text-slate-400")
            ui.button(
                "Ver mis recompensas",
                icon="redeem",
                on_click=lambda: ui.navigate.to(
                    PAGE_ROUTES["mis_recompensas"]
                ),
            ).props("flat no-caps").classes("w-fit text-orange-400")
