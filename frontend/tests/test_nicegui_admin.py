import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, PropertyMock, patch

from pages import nicegui_admin


class NiceGuiAdminTests(unittest.TestCase):
    def test_table_edit_callbacks_open_dialogs_in_page_layout(self):
        record = {"_id": "user-1"}
        layout = MagicMock()
        client = SimpleNamespace(layout=layout)

        with (
            patch.object(
                type(nicegui_admin.context),
                "client",
                new_callable=PropertyMock,
                return_value=client,
            ),
            patch.object(nicegui_admin, "_edit_record") as edit_record,
        ):
            for page_key in ("usuarios", "vehiculos", "tarjetas"):
                callback = nicegui_admin._edit_record_callback(
                    page_key,
                    record,
                    "token",
                    "http://api.test/api",
                )
                callback()

        self.assertEqual(layout.__enter__.call_count, 3)
        self.assertEqual(
            [call.args[0] for call in edit_record.call_args_list],
            ["usuarios", "vehiculos", "tarjetas"],
        )

    def test_company_search_matches_name_and_contact_fields(self):
        companies = [
            {
                "_id": "company-1",
                "nombre": "Carga Norte",
                "contacto_nombre": "Ana López",
                "contacto_email": "ana@example.test",
                "contacto_telefono": "555-0101",
            },
            {"_id": "company-2", "nombre": "Carga Sur"},
        ]

        self.assertEqual(
            [
                company["_id"]
                for company in nicegui_admin._filter_companies(companies, " norte ")
            ],
            ["company-1"],
        )
        self.assertEqual(
            [
                company["_id"]
                for company in nicegui_admin._filter_companies(
                    companies,
                    "555-0101",
                )
            ],
            ["company-1"],
        )
        self.assertEqual(
            nicegui_admin._filter_companies(companies, ""),
            companies,
        )

    def test_qr_operation_search_and_status_labels_include_follow_up_fields(self):
        operations = nicegui_admin._qr_operation_display_rows([
            {
                "id": "request-1",
                "empresa": "Empresa Norte",
                "cliente": "Cliente Uno",
                "vehiculo": "Nissan Leaf",
                "charge_point_id": "CP-QR-1",
                "estado_operacion": "finished",
                "transaction_id": 456,
                "seguimiento": "Sesión finalizada · TX 456",
                "created_at": "2026-01-02T10:25:00",
                "started_at": "2026-01-02T10:30:00",
                "finished_at": "2026-01-02T11:15:00",
            },
        ])

        self.assertEqual(operations[0]["estado_visible"], "Finalizada")
        self.assertEqual(operations[0]["started_at"], "2026-01-02 10:30")
        self.assertEqual(
            nicegui_admin._filter_qr_operations(operations, "nissan"),
            operations,
        )
        self.assertEqual(
            nicegui_admin._filter_qr_operations(operations, "TX 456"),
            operations,
        )
        self.assertEqual(
            nicegui_admin._filter_qr_operations(operations, "no existe"),
            [],
        )

    def test_empty_qr_table_still_renders_its_columns(self):
        table = Mock()

        with patch.object(
            nicegui_admin.ui,
            "table",
            return_value=table,
        ) as create_table:
            nicegui_admin._display_table(
                [],
                fields=("empresa", "estado_visible"),
                labels={
                    "empresa": "Empresa",
                    "estado_visible": "Estado",
                },
                responsive=True,
            )

        options = create_table.call_args.kwargs
        self.assertEqual(options["rows"], [])
        self.assertEqual(
            [column["label"] for column in options["columns"]],
            ["Empresa", "Estado"],
        )

    def test_session_summary_cards_aggregate_count_energy_and_duration(self):
        cards = nicegui_admin._session_summary_cards(
            [
                {
                    "estado": "finalizada",
                    "duracion_minutos": 90,
                    "energia_consumida_kwh": 4.5,
                },
                {
                    "estado": "activa",
                    "duracion_minutos": 0,
                    "energia_consumida_kwh": 5.5,
                },
            ]
        )

        self.assertEqual([card[1] for card in cards], [2, 1, "10 kWh", "90 min"])

    def test_empty_session_summary_uses_zero_and_no_average(self):
        cards = nicegui_admin._session_summary_cards([])

        self.assertEqual([card[1] for card in cards], [0, 0, "0 kWh", "—"])

    def test_reward_summary_cards_aggregate_active_and_monthly_metrics(self):
        cards = nicegui_admin._reward_summary_cards(
            [
                {"activa": True},
                {"activa": False},
                {"activa": True},
            ],
            [{"activa": True}, {"activa": False}],
            [
                {"fecha_otorgamiento": "2026-01-03T10:00:00", "usado": True},
                {"fecha_otorgamiento": "2026-01-20T10:00:00", "usado": False},
                {"fecha_otorgamiento": "2025-12-31T23:59:00", "usado": True},
            ],
            now=datetime(2026, 1, 21),
        )

        self.assertEqual([card[1] for card in cards], [2, 1, 2, "67%"])

    def test_reward_ranking_sorts_by_loads_and_calculates_next_milestone(self):
        rows = nicegui_admin._reward_ranking_rows(
            [
                {"usuario": "Ana", "total_cargas": 2},
                {"usuario": "Beto", "total_cargas": 5},
            ],
            [
                {"usuario": "Ana"},
                {"usuario": "Beto"},
                {"usuario": "Beto"},
            ],
            [
                {"cargas_requeridas": 5, "activa": True},
                {"cargas_requeridas": 10, "activa": True},
                {"cargas_requeridas": 20, "activa": False},
            ],
        )

        self.assertEqual([row["usuario"] for row in rows], ["Beto", "Ana"])
        self.assertEqual(
            [(row["recompensas"], row["progreso"], row["progreso_texto"]) for row in rows],
            [
                (2, 50, "5 cargas para el siguiente hito"),
                (1, 40, "3 cargas para el siguiente hito"),
            ],
        )

    def test_report_period_and_chart_cover_the_full_requested_day_range(self):
        points = nicegui_admin._report_chart_points(
            [{"fecha": "2026-11-13", "energia_kwh": 4.5}],
            "fecha",
            "energia_kwh",
            7,
            today=date(2026, 11, 13),
        )

        self.assertEqual(len(points), 7)
        self.assertEqual(points[0], {"label": "Sáb 7", "value": 0})
        self.assertEqual(points[-1], {"label": "Vie 13", "value": 4.5})
        self.assertEqual(
            nicegui_admin._report_period_range(30, today=date(2026, 11, 13)),
            ("2026-10-15", "2026-11-13"),
        )

    def test_report_csv_download_uses_binary_response_content(self):
        response = {
            "content": b"\xef\xbb\xbfNombre\r\nAna\r\n",
            "filename": "usuarios.csv",
            "content_type": "text/csv; charset=utf-8",
        }
        with (
            patch.object(nicegui_admin.ui, "row"),
            patch.object(nicegui_admin.ui, "button") as buttons,
            patch.object(
                nicegui_admin,
                "api_download",
                return_value=response,
            ) as api_download,
            patch.object(nicegui_admin.ui, "download") as download,
        ):
            nicegui_admin._render_report_export_actions(
                "/reportes/usuarios/export",
                "token",
                "https://api.example.test/api",
            )
            csv_button = buttons.call_args_list[1]
            csv_button.kwargs["on_click"]()

        api_download.assert_called_once_with(
            "/reportes/usuarios/export",
            params={"fmt": "csv"},
            token="token",
            base_url="https://api.example.test/api",
        )
        download.assert_called_once_with(
            response["content"],
            "usuarios.csv",
            "text/csv; charset=utf-8",
        )

    def test_report_tables_paginate_twenty_rows(self):
        self.assertEqual(
            nicegui_admin._dashboard_page_bounds(21, 20, 1),
            (20, 21, 2, 1),
        )

    def test_session_table_values_are_formatted_and_have_stable_folios(self):
        record = {
            "transaction_id": 17,
            "duracion_minutos": 125,
            "energia_consumida_kwh": 6.25,
        }

        self.assertEqual(nicegui_admin._session_folio(record), "S-0017")
        self.assertEqual(nicegui_admin._format_session_duration(125), "2 h 5 min")
        self.assertEqual(nicegui_admin._format_session_energy(6.25), "6.25 kWh")

    def test_ocpp_table_translates_headers_without_truncating_data(self):
        payload = {"detail": "x" * 250}
        table = Mock()

        with patch.object(nicegui_admin.ui, "table", return_value=table) as create_table:
            nicegui_admin._display_table(
                [{"charge_point_id": "CP-1", "payload": payload}],
                row_key="table_row",
                fields=nicegui_admin.OCPP_MESSAGE_TABLE_FIELDS,
                labels=nicegui_admin.OCPP_MESSAGE_TABLE_LABELS,
                column_widths=nicegui_admin.OCPP_MESSAGE_TABLE_WIDTHS,
                truncate_cell_values=False,
                responsive=True,
            )

        table_options = create_table.call_args.kwargs
        self.assertEqual(table_options["columns"][0]["label"], "ID de cargador")
        self.assertEqual(table_options["columns"][-1]["label"], "Datos (payload)")
        self.assertEqual(table_options["rows"][0]["payload"], str(payload))

    def test_ocpp_summary_cards_use_direction_counts_and_error_totals(self):
        cards = nicegui_admin._ocpp_summary_cards(
            [
                {"direction": "incoming"},
                {"direction": "incoming"},
                {"direction": "outgoing"},
            ],
            [{"message_type_name": "CALLERROR"}],
        )

        self.assertEqual([card[1] for card in cards], [3, 2, 1, 1])
        self.assertEqual([card[3] for card in cards], [
            "chat_bubble",
            "call_received",
            "call_made",
            "emergency",
        ])
        self.assertEqual(cards[1][2], "67% del historial")
        self.assertEqual(cards[2][2], "33% del historial")

    def test_ocpp_event_status_and_operator_summary_use_real_message_direction(self):
        incoming = {
            "direction": "incoming",
            "message_type_name": "CALL",
        }
        error = {
            "direction": "outgoing",
            "message_type_name": "CALLERROR",
        }

        self.assertEqual(
            nicegui_admin._ocpp_message_status(incoming),
            ("Correcto", "success"),
        )
        self.assertEqual(
            nicegui_admin._ocpp_operator_summary(incoming),
            (
                "El cargador envió este mensaje al sistema.",
                "El mensaje se registró en el historial.",
            ),
        )
        self.assertEqual(
            nicegui_admin._ocpp_message_status(error),
            ("Error", "error"),
        )

    def test_ocpp_timestamp_shows_time_milliseconds_and_date(self):
        self.assertEqual(
            nicegui_admin._ocpp_timestamp_parts(
                datetime(2026, 9, 9, 20, 10, 20, 284000)
            ),
            ("20:10:20.284", "2026-09-09"),
        )

    def test_ocpp_inspection_handler_opens_details_for_the_selected_message(self):
        message = {"unique_id": "boot-001"}
        dialog = object()
        details_container = object()

        with patch.object(nicegui_admin, "_show_ocpp_event_details") as show_details:
            nicegui_admin._ocpp_inspection_handler(
                message,
                dialog,
                details_container,
            )()

        show_details.assert_called_once_with(
            message,
            dialog,
            details_container,
        )

    def test_charger_communication_status_uses_connection_and_heartbeat_age(self):
        now = datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc)

        self.assertEqual(
            nicegui_admin._charger_communication_status(
                {
                    "conectado": True,
                    "ultimo_heartbeat": (now - timedelta(seconds=60)).isoformat(),
                },
                now=now,
            ),
            ("Comunicación estable", "green"),
        )
        self.assertEqual(
            nicegui_admin._charger_communication_status(
                {
                    "conectado": True,
                    "ultimo_heartbeat": now - timedelta(seconds=180),
                },
                now=now,
            ),
            ("Comunicación inestable", "yellow"),
        )
        self.assertEqual(
            nicegui_admin._charger_communication_status(
                {
                    "conectado": True,
                    "ultimo_heartbeat": now - timedelta(seconds=360),
                },
                now=now,
            ),
            ("Sin comunicación", "red"),
        )
        self.assertEqual(
            nicegui_admin._charger_communication_status(
                {"conectado": False, "ultimo_heartbeat": now},
                now=now,
            ),
            ("Sin comunicación", "red"),
        )

    def test_charger_summary_counts_connection_and_fault_states(self):
        summary = nicegui_admin._charger_summary_cards(
            [
                {"estado_general": "disponible", "conectado": False},
                {"estado_general": "disponible", "conectado": True},
                {"estado_general": "fuera_de_servicio", "conectores": []},
                {
                    "estado_general": "ocupado",
                    "conectores": [{"estado": "Faulted"}],
                },
            ]
        )

        self.assertEqual([item[1] for item in summary], [4, 2, 1, 2])

    def test_user_summary_counts_come_from_loaded_user_records(self):
        counts = nicegui_admin._user_summary_counts(
            [
                {"rol": "superadmin", "activo": True},
                {"rol": "cliente", "activo": True},
                {"rol": "superadmin", "activo": False},
            ]
        )

        self.assertEqual(
            counts,
            {"total": 3, "active": 2, "admins": 2, "inactive": 1},
        )

    def test_summary_cards_use_the_reference_icons_and_labels(self):
        charger_cards = nicegui_admin._charger_summary_cards([])
        nfc_cards = nicegui_admin._registry_summary_cards("tarjetas", [])
        vehicle_cards = nicegui_admin._registry_summary_cards("vehiculos", [])
        user_cards = nicegui_admin._user_summary_cards(
            [{"rol": "superadmin", "activo": True}]
        )

        self.assertEqual(
            [card[3] for card in charger_cards],
            ["ev_station", "check_box", "bar_chart", "warning"],
        )
        self.assertEqual(
            [card[3] for card in nfc_cards],
            ["credit_card", "check_box", "do_not_disturb_on", "pause"],
        )
        self.assertEqual(
            [card[3] for card in vehicle_cards],
            ["directions_car", "sell", "battery_charging_full", "check_box"],
        )
        self.assertEqual(
            [card[3] for card in user_cards],
            ["groups", "check_box", "shield", "do_not_disturb_on"],
        )
        self.assertEqual(nfc_cards[-1][0], "Desactivadas")
        self.assertEqual(vehicle_cards[1][0], "Marcas diferentes")
        self.assertEqual(user_cards[2][0], "Superadministradores")

    def test_vehicle_registry_summary_uses_real_vehicle_fields(self):
        summary = nicegui_admin._registry_summary_cards(
            "vehiculos",
            [
                {
                    "marca": "Nissan",
                    "placa": "ABC-123",
                    "capacidad_bateria_kwh": 50,
                    "tipo_conector": "Type2",
                },
                {
                    "marca": "Nissan",
                    "placa": "",
                    "capacidad_bateria_kwh": None,
                    "tipo_conector": "Type2",
                    "activo": False,
                },
                {
                    "marca": "Tesla",
                    "placa": "XYZ-789",
                    "capacidad_bateria_kwh": 75,
                    "tipo_conector": "CCS",
                },
            ],
        )

        self.assertEqual(
            [item[1] for item in summary],
            [3, 2, "62.5", 2],
        )

    def test_nfc_registry_summary_counts_each_real_card_state(self):
        summary = nicegui_admin._registry_summary_cards(
            "tarjetas",
            [
                {"estado": "activa"},
                {"estado": "bloqueada"},
                {"estado": "perdida"},
                {"estado": "activa"},
            ],
        )

        self.assertEqual(
            [item[1] for item in summary],
            [4, 2, 1, 1],
        )

    def test_empty_history_chart_shows_selected_dates_without_fake_values(self):
        days, values = nicegui_admin._dashboard_history_chart_data(
            {},
            date(2026, 10, 14),
            date(2026, 11, 12),
        )

        self.assertEqual(len(days), 30)
        self.assertEqual(days[0], "2026-10-14")
        self.assertEqual(days[-1], "2026-11-12")
        self.assertEqual(values, [None] * 30)

    def test_history_chart_preserves_measured_energy(self):
        days, values = nicegui_admin._dashboard_history_chart_data(
            {
                "2026-11-11": {
                    "energy": 3.456,
                    "energy_sessions": 1,
                },
                "2026-11-12": {
                    "energy": 0.0,
                    "energy_sessions": 0,
                },
            },
            date(2026, 11, 1),
            date(2026, 11, 12),
        )

        self.assertEqual(days, ["2026-11-11", "2026-11-12"])
        self.assertEqual(values, [3.46, None])

    def test_station_energy_rows_show_registered_chargers_without_fake_values(self):
        chargers = [
            {"charge_point_id": "CP-1", "nombre": "Estación uno"},
            {"charge_point_id": "CP-2", "nombre": "Estación dos"},
        ]

        rows = nicegui_admin._dashboard_station_energy_rows([], chargers)

        self.assertEqual(
            rows,
            [
                (chargers[0], None),
                (chargers[1], None),
            ],
        )

    def test_station_energy_rows_combine_real_energy_with_registered_chargers(self):
        chargers = [
            {"charge_point_id": "CP-1", "nombre": "Estación uno"},
            {"charge_point_id": "CP-2", "nombre": "Estación dos"},
        ]

        rows = nicegui_admin._dashboard_station_energy_rows(
            [{"charge_point_id": "CP-2", "energia_kwh": 4.5}],
            chargers,
        )

        self.assertEqual(
            rows,
            [
                (
                    {
                        "charge_point_id": "CP-2",
                        "nombre": "Estación dos",
                        "energia_kwh": 4.5,
                    },
                    4.5,
                ),
                (chargers[0], None),
            ],
        )

    def test_recent_alert_rows_use_the_new_columns_and_formatted_date(self):
        rows = nicegui_admin._dashboard_alert_table_rows(
            [
                {
                    "_id": "alert-1",
                    "nivel": "warning",
                    "mensaje": "Cargador CP-1 desconectado",
                    "timestamp": "2026-10-12T22:01:59Z",
                }
            ]
        )

        self.assertEqual(
            rows,
            [
                {
                    "fila": "alert-1",
                    "nivel": "Warning",
                    "nivel_class": "warning",
                    "mensaje": "Cargador CP-1 desconectado",
                    "fecha": "2026-10-12 22:01",
                }
            ],
        )

    def test_recent_event_rows_map_charger_event_and_date(self):
        rows = nicegui_admin._dashboard_event_table_rows(
            [
                {
                    "_id": "event-1",
                    "charge_point_id": "CP-1",
                    "tipo_mensaje": "Heartbeat",
                    "timestamp": "2026-10-12T22:17:43",
                }
            ]
        )

        self.assertEqual(
            rows,
            [
                {
                    "fila": "event-1",
                    "cargador": "CP-1",
                    "evento": "Heartbeat",
                    "fecha": "2026-10-12 22:17",
                }
            ],
        )

    def test_empty_dashboard_table_renders_headers_when_columns_are_provided(self):
        class FakeElement:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def classes(self, *_):
                return self

            def props(self, *_):
                return self

        columns = [{"name": "user", "label": "User", "field": "user"}]
        with (
            patch.object(nicegui_admin.ui, "element", return_value=FakeElement()),
            patch.object(
                nicegui_admin.ui,
                "table",
                return_value=FakeElement(),
            ) as table,
            patch.object(nicegui_admin.ui, "label") as label,
        ):
            nicegui_admin._render_dashboard_table(
                [],
                "Unable to load rewards.",
                empty_columns=columns,
            )

        table.assert_called_once_with(
            columns=columns,
            rows=[],
            row_key="user",
        )
        label.assert_not_called()

    def test_dashboard_list_toggles_between_five_and_twenty_rows(self):
        rendered_rows = []
        buttons = {}

        class FakeElement:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def classes(self, *_):
                return self

            def props(self, *_):
                return self

            def clear(self):
                return None

        def create_button(label, on_click=None):
            buttons[label] = on_click
            return FakeElement()

        with (
            patch.object(nicegui_admin.ui, "column", side_effect=FakeElement),
            patch.object(nicegui_admin.ui, "row", side_effect=FakeElement),
            patch.object(nicegui_admin.ui, "label", side_effect=FakeElement),
            patch.object(nicegui_admin.ui, "button", side_effect=create_button),
        ):
            nicegui_admin._render_expandable_dashboard_rows(
                list(range(23)),
                lambda rows: rendered_rows.append(list(rows)),
            )
            buttons["Ver más ↓"]()
            buttons["Siguiente →"]()
            buttons["Ver menos ↑"]()

        self.assertEqual(rendered_rows[0], list(range(5)))
        self.assertEqual(rendered_rows[1], list(range(20)))
        self.assertEqual(rendered_rows[2], [20, 21, 22])
        self.assertEqual(rendered_rows[3], list(range(5)))

    def test_dashboard_pages_show_five_or_twenty_rows_and_clamp_page(self):
        self.assertEqual(
            nicegui_admin._dashboard_page_bounds(23, 5, 0),
            (0, 5, 5, 0),
        )
        self.assertEqual(
            nicegui_admin._dashboard_page_bounds(23, 20, 0),
            (0, 20, 2, 0),
        )
        self.assertEqual(
            nicegui_admin._dashboard_page_bounds(23, 20, 10),
            (20, 23, 2, 1),
        )

    def test_user_actions_do_not_include_charger_controls(self):
        with patch.object(nicegui_admin.ui, "button") as button:
            nicegui_admin._render_record_actions(
                "usuarios",
                {"_id": "user-1"},
                "token",
                "http://api.test/api",
                can_manage=True,
                can_operate=True,
            )

        labels = [call.args[0] for call in button.call_args_list]
        self.assertIn("Editar", labels)
        self.assertIn("Desactivar", labels)
        self.assertIn("Historial", labels)
        self.assertNotIn("Lecturas", labels)
        self.assertNotIn("Cambiar disponibilidad", labels)
        self.assertNotIn("Inicio remoto", labels)
        self.assertNotIn("Reinicio suave", labels)

    def test_operator_can_access_charger_controls_without_admin_edit_actions(self):
        with patch.object(nicegui_admin.ui, "button") as button:
            nicegui_admin._render_record_actions(
                "cargadores",
                {"_id": "charger-1", "charge_point_id": "CP-1"},
                "token",
                "http://api.test/api",
                can_manage=False,
                can_operate=True,
            )

        labels = [call.args[0] for call in button.call_args_list]
        self.assertIn("Ver conexión", labels)
        self.assertIn("Historial", labels)
        self.assertNotIn("Lecturas", labels)
        self.assertIn("Cambiar disponibilidad", labels)
        self.assertIn("Revisar comandos", labels)
        self.assertNotIn("Inicio remoto", labels)
        self.assertIn("Reinicio suave", labels)
        self.assertIn("Reinicio forzado", labels)
        self.assertNotIn("Editar", labels)
        self.assertNotIn("Desactivar", labels)
        self.assertNotIn("Eliminar", labels)

    def test_operator_reconciliation_displays_indeterminate_state(self):
        response = {
            "commands": [{
                "action": "RemoteStopTransaction",
                "status": "unknown",
                "lock_retained": True,
            }],
        }
        with (
            patch.object(nicegui_admin, "api_post", return_value=response) as post,
            patch.object(nicegui_admin.ui, "notify") as notify,
        ):
            nicegui_admin._reconcile_charger_commands(
                {"charge_point_id": "CP-1"},
                "token",
                "http://api.test/api",
            )

        self.assertEqual(
            post.call_args.args[0],
            "/cargadores/CP-1/commands/reconcile",
        )
        self.assertEqual(post.call_args.kwargs["json"], {})
        notify.assert_called_once_with(
            "Resultado de reconciliación: RemoteStopTransaction: unknown",
            type="warning",
        )

    def test_admin_charger_actions_add_delete_without_meter_readings(self):
        with patch.object(nicegui_admin.ui, "button") as button:
            nicegui_admin._render_record_actions(
                "cargadores",
                {"_id": "charger-1", "charge_point_id": "CP-1"},
                "token",
                "http://api.test/api",
                can_manage=True,
                can_operate=True,
            )

        labels = [call.args[0] for call in button.call_args_list]
        self.assertIn("Editar", labels)
        self.assertIn("Desactivar", labels)
        self.assertIn("Eliminar", labels)
        self.assertNotIn("Lecturas", labels)
        self.assertIn("Reinicio suave", labels)
        self.assertIn("Reinicio forzado", labels)

    def test_charger_connection_urls_use_the_api_host_and_encode_the_charger_id(self):
        urls = nicegui_admin._charger_connection_urls(
            "https://chargecore.example.com/api",
            "CP/1 test",
        )

        self.assertEqual(
            urls,
            {
                "wss": "wss://chargecore.example.com/ocpp/CP%2F1%20test",
                "ws": "ws://chargecore.example.com/ocpp/CP%2F1%20test",
            },
        )

    def test_charger_connection_urls_reject_invalid_api_addresses(self):
        for address in ("ftp://chargecore.example.com/api", "https:///api"):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    nicegui_admin._charger_connection_urls(address, "CP-1")

    def test_monitoring_all_levels_filter_omits_empty_level(self):
        params = nicegui_admin._build_list_params(
            "alertas",
            {"dias": 30, "nivel": "", "modulo": "  auth  "},
        )

        self.assertEqual(
            params,
            {"dias": 30, "limit": 100, "modulo": "auth"},
        )

    def test_alert_summary_and_rows_use_translated_levels_and_resources(self):
        alerts = [
            {
                "_id": "alert-1",
                "nivel": "error",
                "modulo": "ocpp",
                "mensaje": "Falla de comunicación",
                "datos": {"charge_point_id": "CP-1"},
                "estado": "attended",
            },
            {"nivel": "warning", "modulo": "cargadores"},
            {"nivel": "info", "modulo": "ocpp"},
        ]

        self.assertEqual(
            nicegui_admin._alert_summary_values(alerts),
            (3, 1, 1, 2),
        )
        row = nicegui_admin._alert_table_rows(alerts)[0]
        self.assertEqual(row["nivel"], "Crítica")
        self.assertEqual(row["nivel_clase"], "critical")
        self.assertEqual(row["titulo"], "Falla de comunicación")
        self.assertEqual(row["recurso"], "CP-1")
        self.assertEqual(row["estado"], "Atendida")

    def test_alert_chart_returns_a_complete_seven_day_range(self):
        points = nicegui_admin._alert_chart_points(
            [{"timestamp": "2026-11-13T12:00:00", "nivel": "error"}],
            today=date(2026, 11, 13),
        )

        self.assertEqual(len(points), 7)
        self.assertEqual(points[0], {"label": "Sáb 7", "value": 0})
        self.assertEqual(points[-1], {"label": "Vie 13", "value": 1})

    def test_event_filter_omits_blank_charger_and_trims_identifier(self):
        params = nicegui_admin._build_list_params(
            "eventos",
            {"dias": 7, "charge_point_id": "  CP-1  "},
        )

        self.assertEqual(
            params,
            {"dias": 7, "limit": 100, "charge_point_id": "CP-1"},
        )

    def test_session_filters_skip_empty_values(self):
        params = nicegui_admin._build_list_params(
            "sesiones",
            {
                "estado": "",
                "charge_point_id": " CP-2 ",
                "fecha_inicio": "",
                "fecha_fin": "2026-10-31",
            },
        )
        self.assertEqual(
            params,
            {
                "limit": 200,
                "charge_point_id": "CP-2",
                "fecha_fin": "2026-10-31",
            },
        )

    def test_user_update_omits_blank_password(self):
        payload = nicegui_admin._build_user_update_payload(
            {"nombre": "Cliente"},
            "",
            "",
        )

        self.assertEqual(payload, {"nombre": "Cliente"})

    def test_user_update_validates_optional_password(self):
        with self.assertRaisesRegex(ValueError, "al menos 6"):
            nicegui_admin._build_user_update_payload(
                {"nombre": "Cliente"},
                "123",
                "123",
            )
        with self.assertRaisesRegex(ValueError, "no coinciden"):
            nicegui_admin._build_user_update_payload(
                {"nombre": "Cliente"},
                "123456",
                "654321",
            )

    def test_user_update_includes_confirmed_password(self):
        payload = nicegui_admin._build_user_update_payload(
            {"nombre": "Cliente"},
            "secret123",
            "secret123",
        )

        self.assertEqual(
            payload,
            {"nombre": "Cliente", "password": "secret123"},
        )

    def test_load_all_users_fetches_all_pages(self):
        first_page = [{"_id": str(index)} for index in range(100)]
        second_page = [{"_id": "100"}]
        with patch.object(
            nicegui_admin,
            "api_get",
            side_effect=[first_page, second_page],
        ) as api_get:
            result = nicegui_admin._load_all_users(
                "token",
                "http://api.test/api",
            )

        self.assertEqual(len(result), 101)
        self.assertEqual(api_get.call_count, 2)
        self.assertEqual(api_get.call_args_list[1].kwargs["params"]["skip"], 100)

    def test_connector_payload_is_validated_and_serialized(self):
        rows = [
            {
                "id_conector": SimpleNamespace(value=1),
                "tipo": SimpleNamespace(value=" Type2 "),
                "potencia_max_kw": SimpleNamespace(value=7.4),
                "estado": SimpleNamespace(value="Available"),
            }
        ]

        self.assertEqual(
            nicegui_admin._serialize_connectors(rows),
            [{
                "id_conector": 1,
                "tipo": "Type2",
                "potencia_max_kw": 7.4,
                "estado": "Available",
            }],
        )

    def test_connector_editor_passes_list_options_to_select(self):
        with (
            patch.object(nicegui_admin.ui, "column"),
            patch.object(nicegui_admin.ui, "label"),
            patch.object(nicegui_admin.ui, "row"),
            patch.object(nicegui_admin.ui, "number"),
            patch.object(nicegui_admin.ui, "input"),
            patch.object(nicegui_admin.ui, "select") as select,
            patch.object(nicegui_admin.ui, "button"),
        ):
            nicegui_admin._build_connector_editor()

        options = select.call_args.args[0]
        self.assertIsInstance(options, list)
        self.assertIn("Available", options)
        self.assertIn("Occupied", options)
        self.assertIn("Faulted", options)

    def test_connector_payload_rejects_duplicate_ids(self):
        connector = {
            "id_conector": SimpleNamespace(value=1),
            "tipo": SimpleNamespace(value="Type2"),
            "potencia_max_kw": SimpleNamespace(value=7.4),
            "estado": SimpleNamespace(value="Available"),
        }

        with self.assertRaisesRegex(ValueError, "IDs de conector repetidos"):
            nicegui_admin._serialize_connectors([connector, connector])


if __name__ == "__main__":
    unittest.main()
