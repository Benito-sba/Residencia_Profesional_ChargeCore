import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

from app.config import settings
from app.models.company import Empresa, normalize_company_name


INITIAL_COMPANY_NAME = "TECNM"
COMPANY_NAME_INDEX = "unique_company_normalized_name"


async def ensure_initial_company(companies):
    normalized_name = normalize_company_name(INITIAL_COMPANY_NAME)
    await companies.create_index(
        "nombre_normalizado",
        unique=True,
        name=COMPANY_NAME_INDEX,
        partialFilterExpression={"nombre_normalizado": {"$type": "string"}},
    )

    name_query = {
        "nombre": {
            "$regex": rf"^\s*{re.escape(INITIAL_COMPANY_NAME)}\s*$",
            "$options": "i",
        }
    }
    matching_names = await companies.count_documents(name_query)
    if matching_names > 1:
        raise RuntimeError(
            "Hay varias empresas con el nombre TECNM; requiere revisión manual."
        )

    existing = await companies.find_one({"nombre_normalizado": normalized_name})
    if existing is not None:
        return existing["_id"], False

    if matching_names:
        raise RuntimeError(
            "Existe una empresa TECNM con nombre_normalizado inconsistente; "
            "requiere revisión manual antes de registrar otra."
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    company = Empresa(
        nombre=INITIAL_COMPANY_NAME,
        nombre_normalizado=normalized_name,
        activo=True,
        created_at=now,
        updated_at=now,
    )
    document = company.model_dump(by_alias=True, exclude={"id"})
    try:
        result = await companies.insert_one(document)
        return result.inserted_id, True
    except DuplicateKeyError:
        existing = await companies.find_one({"nombre_normalizado": normalized_name})
        if existing is not None:
            return existing["_id"], False
        raise


async def _register_initial_company():
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    try:
        _, created = await ensure_initial_company(
            client[settings.DATABASE_NAME]["empresas"]
        )
    finally:
        client.close()

    print("Empresa TECNM registrada." if created else "Empresa TECNM ya registrada.")


def main():
    parser = argparse.ArgumentParser(description="Registrar la empresa inicial TECNM.")
    parser.add_argument(
        "--confirm-register-tecnm",
        action="store_true",
        required=True,
        help="Confirma explícitamente el registro de TECNM en MongoDB.",
    )
    parser.parse_args()
    asyncio.run(_register_initial_company())


if __name__ == "__main__":
    main()
