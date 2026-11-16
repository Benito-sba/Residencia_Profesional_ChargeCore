import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import SecretStr

from app.routers import auth as auth_router


class PasswordResetRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_user_request_notifies_configured_admin(self):
        user = {
            "nombre": "Cliente",
            "email": "cliente@example.com",
            "activo": True,
        }
        settings_document = {"recipient_email": "admin@example.com"}
        collections = {
            "usuarios": SimpleNamespace(find_one=AsyncMock(return_value=user)),
            "system_settings": SimpleNamespace(
                find_one=AsyncMock(return_value=settings_document)
            ),
        }
        request = auth_router.PasswordResetRequest(email="cliente@example.com")

        with (
            patch.object(
                auth_router,
                "get_collection",
                side_effect=collections.__getitem__,
            ),
            patch.object(
                auth_router,
                "settings",
                SimpleNamespace(smtp_configured=True),
            ),
            patch.object(
                auth_router.asyncio,
                "to_thread",
                new_callable=AsyncMock,
            ) as send_mail,
        ):
            result = await auth_router.password_reset_request(request)

        self.assertIn("Si la cuenta existe", result["mensaje"])
        send_mail.assert_awaited_once_with(
            auth_router._send_password_reset_notice,
            "admin@example.com",
            "Cliente",
            "cliente@example.com",
        )

    async def test_unknown_user_gets_generic_response_without_email(self):
        collections = {
            "usuarios": SimpleNamespace(find_one=AsyncMock(return_value=None)),
            "system_settings": SimpleNamespace(
                find_one=AsyncMock(
                    return_value={"recipient_email": "admin@example.com"}
                )
            ),
        }
        request = auth_router.PasswordResetRequest(email="unknown@example.com")

        with (
            patch.object(
                auth_router,
                "get_collection",
                side_effect=collections.__getitem__,
            ),
            patch.object(
                auth_router,
                "settings",
                SimpleNamespace(smtp_configured=True),
            ),
            patch.object(
                auth_router.asyncio,
                "to_thread",
                new_callable=AsyncMock,
            ) as send_mail,
        ):
            result = await auth_router.password_reset_request(request)

        self.assertIn("Si la cuenta existe", result["mensaje"])
        send_mail.assert_not_awaited()

    async def test_existing_user_request_requires_admin_recipient(self):
        collections = {
            "usuarios": SimpleNamespace(
                find_one=AsyncMock(
                    return_value={
                        "nombre": "Cliente",
                        "email": "cliente@example.com",
                        "activo": True,
                    }
                )
            ),
            "system_settings": SimpleNamespace(
                find_one=AsyncMock(return_value=None)
            ),
        }
        request = auth_router.PasswordResetRequest(email="cliente@example.com")

        with (
            patch.object(
                auth_router,
                "get_collection",
                side_effect=collections.__getitem__,
            ),
            patch.object(
                auth_router,
                "settings",
                SimpleNamespace(smtp_configured=True),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await auth_router.password_reset_request(request)

        self.assertEqual(raised.exception.status_code, 503)


class PasswordResetRecipientSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_superadmin_can_read_and_update_recipient(self):
        collection = SimpleNamespace(
            find_one=AsyncMock(
                return_value={"recipient_email": "old@example.com"}
            ),
            update_one=AsyncMock(),
        )

        with patch.object(auth_router, "get_collection", return_value=collection):
            current = await auth_router.get_password_reset_recipient({})
            result = await auth_router.update_password_reset_recipient(
                auth_router.PasswordResetRecipientUpdate(email="new@example.com"),
                {},
            )

        self.assertEqual(current, {"email": "old@example.com"})
        self.assertEqual(result["mensaje"], "Correo receptor actualizado")
        collection.update_one.assert_awaited_once_with(
            {"_id": "password_reset"},
            {"$set": {"recipient_email": "new@example.com"}},
            upsert=True,
        )


class PasswordResetEmailTests(unittest.TestCase):
    def test_notice_uses_configured_smtp_and_contains_no_password(self):
        configuration = SimpleNamespace(
            SMTP_FROM_EMAIL="chargecore@example.com",
            SMTP_HOST="smtp.example.com",
            SMTP_PORT=587,
            SMTP_STARTTLS=True,
            SMTP_USE_SSL=False,
            SMTP_USERNAME="chargecore@example.com",
            SMTP_PASSWORD=SecretStr("smtp-secret"),
            smtp_configured=True,
        )

        with (
            patch.object(auth_router, "settings", configuration),
            patch.object(auth_router.smtplib, "SMTP") as smtp_class,
        ):
            server = smtp_class.return_value.__enter__.return_value
            auth_router._send_password_reset_notice(
                "admin@example.com",
                "Cliente",
                "cliente@example.com",
            )

        server.starttls.assert_called_once()
        server.login.assert_called_once_with("chargecore@example.com", "smtp-secret")
        message = server.send_message.call_args.args[0]
        self.assertEqual(message["To"], "admin@example.com")
        self.assertIn("cliente@example.com", message.get_content())
        self.assertNotIn("smtp-secret", message.get_content())


class ChangePasswordTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_can_change_password_with_current_password(self):
        current_hash = "verified-hash"
        updated_collection = SimpleNamespace(update_one=AsyncMock())
        current_user = {"_id": "user-id", "password": current_hash}
        request = auth_router.PasswordChangeRequest(
            current_password="old-password",
            new_password="new-password",
        )

        with (
            patch.object(
                auth_router,
                "verify_password",
                side_effect=lambda value, hashed: (
                    value == "old-password" and hashed == current_hash
                ),
            ),
            patch.object(
                auth_router,
                "get_password_hash",
                return_value="new-hash",
            ),
            patch.object(
                auth_router,
                "get_collection",
                return_value=updated_collection,
            ),
        ):
            result = await auth_router.change_password(request, current_user)

        self.assertEqual(result["mensaje"], "Contraseña actualizada correctamente")
        updated_collection.update_one.assert_awaited_once_with(
            {"_id": "user-id"},
            {"$set": {"password": "new-hash"}},
        )

    async def test_change_password_rejects_incorrect_current_password(self):
        current_user = {"_id": "user-id", "password": "verified-hash"}
        request = auth_router.PasswordChangeRequest(
            current_password="wrong-password",
            new_password="new-password",
        )

        with patch.object(auth_router, "verify_password", return_value=False):
            with self.assertRaises(HTTPException) as raised:
                await auth_router.change_password(request, current_user)

        self.assertEqual(raised.exception.status_code, 400)
