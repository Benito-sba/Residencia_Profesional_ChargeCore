import unittest
from datetime import datetime, timedelta
import re
from unittest.mock import patch

from bson import ObjectId
from fastapi import HTTPException
from reportlab.lib.pagesizes import A6, letter
from reportlab.lib.units import mm

from app.routers import client_portal


class TicketCollection:
    def __init__(self, documents):
        self.documents = documents

    async def find_one(self, query, projection=None):
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return document
        return None


class ClientPortalTicketTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user_id = ObjectId()
        self.company_id = ObjectId()
        self.session_id = ObjectId()
        self.vehicle_id = ObjectId()
        self.session = {
            "_id": self.session_id,
            "usuario_id": self.user_id,
            "empresa_id": self.company_id,
            "vehiculo_id": self.vehicle_id,
            "charge_point_id": "CP-1",
            "connector_id": 1,
            "fecha_inicio": datetime.utcnow() - timedelta(minutes=30),
            "fecha_fin": datetime.utcnow(),
            "duracion_minutos": 30,
            "energia_consumida_kwh": 4.25,
            "potencia_promedio_kw": 8.5,
            "estado": "finalizada",
            "motivo_fin": "Normal",
        }
        self.collections = {
            "sesiones": TicketCollection([self.session]),
            "vehiculos": TicketCollection([{
                "_id": self.vehicle_id,
                "usuario_id": self.user_id,
                "empresa_id": self.company_id,
                "marca": "ChargeCore",
                "modelo": "EV",
                "placa": "ABC123",
            }]),
            "cargadores": TicketCollection([{
                "charge_point_id": "CP-1",
                "empresa_id": self.company_id,
                "nombre": "Cargador de prueba",
            }]),
            "recompensas": TicketCollection([]),
        }

    async def render_ticket(self, formato="recibo"):
        with patch.object(
            client_portal,
            "get_collection",
            side_effect=self.collections.__getitem__,
        ):
            response = await client_portal.download_client_session_ticket(
                str(self.session_id),
                formato=formato,
                current_user={
                    "_id": self.user_id,
                    "empresa_id": self.company_id,
                    "nombre": "Cliente",
                },
            )

        content = b"".join([
            chunk async for chunk in response.body_iterator
        ])
        return response, content

    def page_size(self, content):
        match = re.search(
            rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]",
            content,
        )
        self.assertIsNotNone(match)
        return tuple(float(value) for value in match.groups())

    async def test_default_ticket_is_a_content_sized_80mm_receipt(self):
        response, content = await self.render_ticket()

        self.assertEqual(response.media_type, "application/pdf")
        self.assertIn("-recibo.pdf", response.headers["content-disposition"])
        self.assertTrue(content.startswith(b"%PDF"))
        width, height = self.page_size(content)
        self.assertAlmostEqual(width, 80 * mm, places=1)
        self.assertGreater(height, width)
        self.assertIn(b"Gracias por realizar su carga con ChargeCore", content)
        self.assertNotIn(b"Comprobante informativo", content)
        self.assertNotIn(b"GANASTE UNA RECOMPENSA", content)

    async def test_receipt_centers_qr_over_ticket_code_label(self):
        _, content = await self.render_ticket()

        self.assertIn(b".75 0 0 .75 65.19685 0 cm", content)

    async def test_ticket_supports_centered_a6_and_letter_formats(self):
        for formato, expected_size in (("a6", A6), ("carta", letter)):
            response, content = await self.render_ticket(formato)
            self.assertIn(f"-{formato}.pdf", response.headers["content-disposition"])
            width, height = self.page_size(content)
            self.assertAlmostEqual(width, expected_size[0], places=1)
            self.assertAlmostEqual(height, expected_size[1], places=1)

    async def test_reward_ticket_includes_reward_details_and_additional_height(self):
        _, standard_pdf = await self.render_ticket()
        self.session["beneficio_aplicado"] = "Beneficio especial por recompensa"
        self.collections["recompensas"] = TicketCollection([{
            "usuario_id": self.user_id,
            "sesion_id_uso": self.session_id,
            "snapshot_nombre": "10% de descuento",
            "snapshot_empresa": "Restaurante El Sabor",
            "snapshot_descripcion": "Descuento en consumos participantes.",
            "snapshot_condiciones": "Válido durante noviembre.",
        }])

        _, reward_pdf = await self.render_ticket()

        self.assertIn(b"GANASTE UNA RECOMPENSA", reward_pdf)
        self.assertIn(b"10% de descuento", reward_pdf)
        self.assertIn(b"Restaurante El Sabor", reward_pdf)
        self.assertGreater(
            self.page_size(reward_pdf)[1],
            self.page_size(standard_pdf)[1],
        )

    async def test_ticket_rejects_an_unsupported_format(self):
        with self.assertRaises(HTTPException) as raised:
            await client_portal.download_client_session_ticket(
                str(self.session_id),
                formato="legal",
                current_user={
                    "_id": self.user_id,
                    "empresa_id": self.company_id,
                    "nombre": "Cliente",
                },
            )

        self.assertEqual(raised.exception.status_code, 422)

    async def test_ticket_is_not_available_before_session_finishes(self):
        self.session["estado"] = "activa"
        with patch.object(
            client_portal,
            "get_collection",
            side_effect=self.collections.__getitem__,
        ):
            with self.assertRaises(HTTPException) as raised:
                await client_portal.download_client_session_ticket(
                    str(self.session_id),
                    current_user={
                        "_id": self.user_id,
                        "empresa_id": self.company_id,
                        "nombre": "Cliente",
                    },
                )

        self.assertEqual(raised.exception.status_code, 409)

    async def test_ticket_does_not_expose_another_clients_session(self):
        with patch.object(
            client_portal,
            "get_collection",
            side_effect=self.collections.__getitem__,
        ):
            with self.assertRaises(HTTPException) as raised:
                await client_portal.download_client_session_ticket(
                    str(self.session_id),
                    current_user={
                        "_id": ObjectId(),
                        "empresa_id": self.company_id,
                        "nombre": "Otro cliente",
                    },
                )

        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
