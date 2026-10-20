from functools import lru_cache
import ipaddress
import re
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "CSMS CRODE Mérida"
    DEBUG: bool = True
    MONGODB_URL: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = "csms_crode"
    SUPABASE_URL: Optional[str] = None
    SUPABASE_SECRET_KEY: Optional[SecretStr] = Field(default=None, repr=False)
    SECRET_KEY: SecretStr = Field(..., repr=False)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480
    OCPP_COMMAND_TIMEOUT_SECONDS: float = 30.0
    CORS_ORIGINS: str = ""

    @model_validator(mode="after")
    def validate_secret_key(self):
        if len(self.SECRET_KEY.get_secret_value()) < 32:
            raise RuntimeError("SECRET_KEY debe tener al menos 32 caracteres")
        if not self.DEBUG:
            parsed = urlsplit(self.MONGODB_URL)
            options = {
                key.lower(): values
                for key, values in parse_qs(parsed.query).items()
            }
            tls_values = [
                value.lower() in {"true", "1"}
                for key in ("tls", "ssl")
                for value in options.get(key, [])
            ]
            tls_enabled = (
                (parsed.scheme == "mongodb+srv" or bool(tls_values))
                and (not tls_values or all(tls_values))
                and not any(
                    value.lower() in {"true", "1"}
                    for key in ("tlsallowinvalidcertificates", "tlsinsecure")
                    for value in options.get(key, [])
                )
            )
            if not tls_enabled:
                raise RuntimeError(
                    "MONGODB_URL debe usar TLS validado cuando DEBUG=false"
                )
        return self

    @field_validator("CORS_ORIGINS")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        if not value.strip():
            return ""

        origins = [origin.strip() for origin in value.split(",")]
        if any(not origin for origin in origins):
            raise ValueError("CORS_ORIGINS contiene un origen vacío")

        normalized_origins = []
        for origin in origins:
            if "*" in origin:
                raise ValueError("CORS_ORIGINS no puede contener comodines")
            try:
                parsed = urlsplit(origin)
                port = parsed.port
            except ValueError as error:
                raise ValueError("CORS_ORIGINS contiene un origen inválido") from error

            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS_ORIGINS debe contener orígenes HTTP(S) válidos")

            hostname = parsed.hostname.lower()
            try:
                ipaddress.ip_address(hostname)
            except ValueError:
                labels = hostname.split(".")
                label_pattern = re.compile(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                )
                if (
                    hostname != "localhost"
                    and (
                        re.fullmatch(r"[0-9.]+", hostname)
                        or not all(label_pattern.fullmatch(label) for label in labels)
                    )
                ):
                    raise ValueError(
                        "CORS_ORIGINS contiene un nombre de host inválido"
                    )

            if port == 0:
                raise ValueError("CORS_ORIGINS contiene un puerto inválido")

            authority = hostname
            if ":" in authority and not authority.startswith("["):
                authority = f"[{authority}]"
            if port is not None:
                authority = f"{authority}:{port}"
            normalized_origins.append(f"{parsed.scheme}://{authority}")

        return ",".join(dict.fromkeys(normalized_origins))

    @property
    def cors_origins(self) -> list[str]:
        if not self.CORS_ORIGINS:
            return []
        return self.CORS_ORIGINS.split(",")

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings():
    return Settings()


settings = get_settings()