from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)
_client: Client | None = None


def connect_supabase() -> None:
    global _client
    _client = None

    url = settings.SUPABASE_URL
    secret_key = settings.SUPABASE_SECRET_KEY
    if not url and not secret_key:
        logger.info("Supabase no está configurado; MongoDB continúa como base activa")
        return
    if not url or not secret_key:
        raise RuntimeError(
            "SUPABASE_URL y SUPABASE_SECRET_KEY deben configurarse juntos"
        )

    from supabase import create_client

    client = create_client(url, secret_key.get_secret_value())
    client.table("cargadores").select("id").limit(1).execute()
    _client = client
    logger.info("Conexión con Supabase verificada")


def get_supabase_client() -> Client:
    if _client is None:
        raise RuntimeError(
            "Supabase no está conectado. Configura sus variables y reinicia el backend."
        )
    return _client
