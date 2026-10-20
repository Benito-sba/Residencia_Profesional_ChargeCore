import asyncio
import getpass
import os
import sys
from pathlib import Path
from datetime import datetime

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.resolve()))

from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings
from app.auth import get_password_hash
from app.models.user import UsuarioCreate


INITIAL_ADMIN_EMAIL = os.getenv("INITIAL_ADMIN_EMAIL", "admin@crode.merida").strip()


def _prompt_admin_password(email: str) -> str:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError(
            "La creación del primer administrador requiere una terminal interactiva."
        )

    password = getpass.getpass("Contraseña para el primer administrador: ")
    confirmation = getpass.getpass("Confirma la contraseña: ")
    if password != confirmation:
        raise ValueError("Las contraseñas no coinciden; no se creó el administrador.")

    try:
        return UsuarioCreate(
            nombre="Administrador CRODE",
            email=email,
            telefono="9991234567",
            rol="admin",
            activo=True,
            password=password,
        ).password
    except ValidationError as error:
        raise ValueError(
            "La contraseña no cumple los requisitos; no se creó el administrador."
        ) from error

async def init():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DATABASE_NAME]

    try:
        print("🔧 Creando índices...")

        await db.usuarios.create_index("email", unique=True)
        await db.nfc_cards.create_index("uid", unique=True)
        await db.cargadores.create_index("charge_point_id", unique=True)
        await db.sesiones.create_index([("usuario_id", 1), ("estado", 1)])
        await db.sesiones.create_index([("charge_point_id", 1), ("estado", 1)])
        await db.heartbeats.create_index([("charge_point_id", 1), ("timestamp", -1)])

        print("✅ Índices creados")

        admin_exists = await db.usuarios.find_one({"rol": "admin"})
        if not admin_exists:
            password = _prompt_admin_password(INITIAL_ADMIN_EMAIL)
            admin = UsuarioCreate(
                nombre="Administrador CRODE",
                email=INITIAL_ADMIN_EMAIL,
                telefono="9991234567",
                rol="admin",
                activo=True,
                password=password,
            )
            user = admin.model_dump()
            user["password"] = get_password_hash(admin.password)
            user["total_cargas"] = 0
            await db.usuarios.insert_one(user)
            print("✅ Usuario administrador creado")
        else:
            print("ℹ️ Ya existe un administrador; no se modificó ningún usuario")

        print("🏁 Inicialización completa")
    finally:
        client.close()

if __name__ == "__main__":
    asyncio.run(init())