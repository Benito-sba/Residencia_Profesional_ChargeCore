import asyncio
import getpass
import importlib
import io
import os
import secrets
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from unittest.mock import patch

from bson import ObjectId
from fastapi import Depends, FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import JWTError, jwt
from pydantic import ValidationError
from starlette.middleware.cors import CORSMiddleware

from app import auth
from app.config import Settings, settings
from app.main import app as chargecore_app
from app.routers import auth as auth_router
import init_db


class JWTConfigurationTests(unittest.TestCase):
    def test_secret_key_is_required_and_loaded_from_environment(self):
        self.assertTrue(Settings.model_fields["SECRET_KEY"].is_required())
        self.assertIn("SECRET_KEY", settings.model_fields_set)
        self.assertTrue(os.environ.get("SECRET_KEY"))

    def test_missing_secret_key_fails_without_exposing_a_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError) as raised:
                Settings(_env_file=None)

        error = str(raised.exception)
        self.assertIn("SECRET_KEY", error)

    def test_short_secret_key_is_rejected_without_echoing_its_value(self):
        short_key = secrets.token_urlsafe(4)
        with self.assertRaises(RuntimeError) as raised:
            Settings(SECRET_KEY=short_key, _env_file=None)

        self.assertNotIn(short_key, str(raised.exception))

    def test_tokens_use_the_configured_key_and_reject_another_key(self):
        token = auth.create_access_token({"sub": "isolated-test-user"})
        payload = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[settings.ALGORITHM],
        )
        self.assertEqual(payload["sub"], "isolated-test-user")

        other_key = secrets.token_urlsafe(48)
        with self.assertRaises(JWTError):
            jwt.decode(token, other_key, algorithms=[settings.ALGORITHM])

    def test_production_database_uri_requires_tls(self):
        with self.assertRaisesRegex(RuntimeError, "TLS"):
            Settings(
                SECRET_KEY=secrets.token_urlsafe(48),
                DEBUG=False,
                MONGODB_URL="mongodb://db.example:27017/csms",
                _env_file=None,
            )
        for uri in (
            "mongodb+srv://db.example/csms?tls=false",
            "mongodb://db.example:27017/csms?tls=true&tlsInsecure=true",
        ):
            with self.subTest(uri=uri):
                with self.assertRaisesRegex(RuntimeError, "TLS"):
                    Settings(
                        SECRET_KEY=secrets.token_urlsafe(48),
                        DEBUG=False,
                        MONGODB_URL=uri,
                        _env_file=None,
                    )

        secure_settings = Settings(
            SECRET_KEY=secrets.token_urlsafe(48),
            DEBUG=False,
            MONGODB_URL="mongodb+srv://db.example/csms",
            _env_file=None,
        )
        self.assertFalse(secure_settings.DEBUG)


class ActiveAccountAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_inactive_account_cannot_use_an_existing_token(self):
        user_id = ObjectId()

        class UserCollection:
            async def find_one(self, query):
                if query == {"_id": user_id}:
                    return {"_id": user_id, "activo": False, "rol": "cliente"}
                return None

        token = auth.create_access_token({"sub": str(user_id)})
        with patch.object(auth, "get_collection", return_value=UserCollection()):
            with self.assertRaises(HTTPException) as raised:
                await auth.get_current_user(token)

        self.assertEqual(raised.exception.status_code, 401)

    async def test_active_account_can_use_its_token(self):
        user_id = ObjectId()
        user = {"_id": user_id, "activo": True, "rol": "cliente"}

        class UserCollection:
            async def find_one(self, query):
                return user if query == {"_id": user_id} else None

        token = auth.create_access_token({"sub": str(user_id)})
        with patch.object(auth, "get_collection", return_value=UserCollection()):
            resolved_user = await auth.get_current_user(token)

        self.assertEqual(resolved_user, user)


class CorsConfigurationTests(unittest.TestCase):
    def _client(self, origins):
        app = FastAPI()

        @app.get("/")
        async def root():
            return {"status": "ok"}

        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )
        return TestClient(app)

    def test_configured_origin_is_allowed_and_other_origin_is_rejected(self):
        configuration = Settings(
            SECRET_KEY=secrets.token_urlsafe(48),
            CORS_ORIGINS="http://localhost:8501",
            _env_file=None,
        )
        with self._client(configuration.cors_origins) as client:
            allowed = client.get("/", headers={"Origin": "http://localhost:8501"})
            denied = client.get("/", headers={"Origin": "http://untrusted.example"})

        self.assertEqual(
            allowed.headers.get("access-control-allow-origin"),
            "http://localhost:8501",
        )
        self.assertNotIn("access-control-allow-origin", denied.headers)

    def test_empty_cors_setting_allows_no_cross_origin_requests(self):
        configuration = Settings(
            SECRET_KEY=secrets.token_urlsafe(48),
            CORS_ORIGINS="",
            _env_file=None,
        )
        self.assertEqual(configuration.cors_origins, [])

    def test_wildcard_and_invalid_origins_are_rejected(self):
        for origins in (
            "*",
            "http://localhost:8501/path",
            "http://good.example,",
            "http://bad host",
            "http://localhost:99999",
        ):
            with self.subTest(origins=origins):
                with self.assertRaises(ValidationError):
                    Settings(
                        SECRET_KEY=secrets.token_urlsafe(48),
                        CORS_ORIGINS=origins,
                        _env_file=None,
                    )

    def test_chargecore_api_uses_empty_allowlist_without_credentials(self):
        cors = next(
            middleware
            for middleware in chargecore_app.user_middleware
            if middleware.cls is CORSMiddleware
        )
        self.assertEqual(cors.kwargs["allow_origins"], settings.cors_origins)
        self.assertFalse(cors.kwargs["allow_credentials"])


class FakeCollection:
    def __init__(self, existing_admin=None):
        self.existing_admin = existing_admin
        self.index_calls = []
        self.insert_calls = []

    async def create_index(self, *args, **kwargs):
        self.index_calls.append((args, kwargs))

    async def find_one(self, query):
        if query == {"rol": "admin"}:
            return self.existing_admin
        return None

    async def insert_one(self, document):
        self.insert_calls.append(document)


class FakeDatabase:
    def __init__(self, users):
        self.usuarios = users
        self.nfc_cards = FakeCollection()
        self.cargadores = FakeCollection()
        self.sesiones = FakeCollection()
        self.heartbeats = FakeCollection()


class FakeClient:
    def __init__(self, database):
        self.database = database
        self.closed = False

    def __getitem__(self, name):
        return self.database

    def close(self):
        self.closed = True


class TTYInput(io.StringIO):
    def isatty(self):
        return True


class TTYOutput(io.StringIO):
    def isatty(self):
        return True


class InitialAdminProvisioningTests(unittest.TestCase):
    def _run_init(self, users, *, prompt_values=None):
        database = FakeDatabase(users)
        client = FakeClient(database)
        output = TTYOutput()
        patches = [
            patch.object(init_db, "AsyncIOMotorClient", return_value=client),
            redirect_stdout(output),
        ]
        if prompt_values is not None:
            patches.extend([
                patch.object(init_db.sys, "stdin", TTYInput()),
                patch.object(
                    getpass,
                    "getpass",
                    side_effect=prompt_values,
                ),
            ])
        return database, client, output, patches

    def test_existing_admin_is_preserved_without_prompt_or_insert(self):
        existing = {
            "_id": ObjectId(),
            "rol": "admin",
            "password": "existing-hash-placeholder",
        }
        users = FakeCollection(existing)
        database, client, output, patches = self._run_init(users)
        with patches[0], patches[1], patch.object(
            getpass, "getpass", side_effect=AssertionError("unexpected prompt")
        ):
            asyncio.run(init_db.init())

        self.assertEqual(users.existing_admin, existing)
        self.assertEqual(users.insert_calls, [])
        self.assertTrue(client.closed)
        self.assertNotIn(existing["password"], output.getvalue())

    def test_first_admin_password_is_hidden_validated_and_hashed(self):
        password = secrets.token_urlsafe(18)
        users = FakeCollection()
        database, client, output, patches = self._run_init(
            users, prompt_values=[password, password]
        )
        with patches[0], patches[1], patches[2], patches[3]:
            asyncio.run(init_db.init())

        self.assertEqual(len(users.insert_calls), 1)
        created = users.insert_calls[0]
        self.assertTrue(auth.verify_password(password, created["password"]))
        self.assertNotEqual(created["password"], password)
        self.assertNotIn(password, output.getvalue())
        self.assertNotIn(created["password"], output.getvalue())
        self.assertTrue(client.closed)
        self.assertEqual(len(database.usuarios.index_calls), 1)

    def test_mismatched_password_confirmation_cancels_creation(self):
        users = FakeCollection()
        _, client, _, patches = self._run_init(
            users,
            prompt_values=[secrets.token_urlsafe(18), secrets.token_urlsafe(18)],
        )
        with patches[0], patches[1], patches[2], patches[3]:
            with self.assertRaisesRegex(ValueError, "no coinciden"):
                asyncio.run(init_db.init())

        self.assertEqual(users.insert_calls, [])
        self.assertTrue(client.closed)

    def test_importing_initializer_does_not_run_database_initialization(self):
        with patch("motor.motor_asyncio.AsyncIOMotorClient") as client_constructor:
            importlib.reload(init_db)

        client_constructor.assert_not_called()


class LoginCompatibilityTests(unittest.TestCase):
    def test_login_response_remains_compatible_with_streamlit(self):
        password = secrets.token_urlsafe(18)
        user = {
            "_id": ObjectId(),
            "nombre": "Prueba aislada",
            "email": "login-test@example.invalid",
            "password": auth.get_password_hash(password),
            "rol": "admin",
            "activo": True,
        }

        class Users:
            async def find_one(self, query):
                return user if query == {"email": user["email"]} else None

        app = FastAPI()
        app.include_router(auth_router.router)
        with (
            patch.object(auth_router, "get_collection", return_value=Users()),
            TestClient(app) as client,
        ):
            response = client.post(
                "/api/auth/login",
                data={"username": user["email"], "password": password},
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertIn("access_token", result)
        self.assertEqual(result["token_type"], "bearer")
        self.assertEqual(result["user"]["id"], str(user["_id"]))

    def test_invalid_password_and_inactive_user_have_expected_status_codes(self):
        password = secrets.token_urlsafe(18)
        user = {
            "_id": ObjectId(),
            "nombre": "Prueba aislada",
            "email": "login-status@example.invalid",
            "password": auth.get_password_hash(password),
            "rol": "admin",
            "activo": True,
        }

        class Users:
            async def find_one(self, query):
                return user if query == {"email": user["email"]} else None

        app = FastAPI()
        app.include_router(auth_router.router)
        with (
            patch.object(auth_router, "get_collection", return_value=Users()),
            TestClient(app) as client,
        ):
            invalid = client.post(
                "/api/auth/login",
                data={"username": user["email"], "password": "wrong-password"},
            )
            user["activo"] = False
            inactive = client.post(
                "/api/auth/login",
                data={"username": user["email"], "password": password},
            )

        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(inactive.status_code, 403)

    def test_expired_token_is_rejected_before_user_lookup(self):
        app = FastAPI()

        @app.get("/protected")
        async def protected(user=Depends(auth.require_admin)):
            return {"id": str(user["_id"])}

        token = auth.create_access_token(
            {"sub": str(ObjectId()), "rol": "admin"},
            expires_delta=timedelta(seconds=-1),
        )
        with (
            patch.object(
                auth,
                "get_collection",
                side_effect=AssertionError("Expired token must not query MongoDB"),
            ),
            TestClient(app) as client,
        ):
            response = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )

        self.assertEqual(response.status_code, 401)

    def test_admin_route_rejects_read_only_role_and_accepts_admin(self):
        user = {"_id": ObjectId(), "rol": "consulta"}

        class Users:
            async def find_one(self, query):
                return user if query == {"_id": user["_id"]} else None

        app = FastAPI()

        @app.get("/protected")
        async def protected(current_user=Depends(auth.require_admin)):
            return {"id": str(current_user["_id"])}

        token = auth.create_access_token({"sub": str(user["_id"])})
        with (
            patch.object(auth, "get_collection", return_value=Users()),
            TestClient(app) as client,
        ):
            denied = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
            user["rol"] = "admin"
            allowed = client.get(
                "/protected",
                headers={"Authorization": f"Bearer {token}"},
            )

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
