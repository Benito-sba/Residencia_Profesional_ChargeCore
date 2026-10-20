import base64
import hashlib
import hmac
import secrets


_PBKDF2_ROUNDS = 310_000


def hash_ocpp_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ROUNDS,
    )
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_ocpp_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds_text, salt_text, digest_text = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        rounds = int(rounds_text)
        if rounds < 100_000 or rounds > 1_000_000:
            return False
        salt = bytes.fromhex(salt_text)
        expected = bytes.fromhex(digest_text)
    except (AttributeError, TypeError, ValueError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        rounds,
    )
    return hmac.compare_digest(actual, expected)


def parse_ocpp_basic_auth(header: str | None) -> tuple[str, str] | None:
    if not isinstance(header, str):
        return None
    scheme, separator, encoded = header.partition(" ")
    if not separator or scheme.lower() != "basic":
        return None
    try:
        credentials = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, separator, password = credentials.partition(":")
    if not separator or not username or not password:
        return None
    return username, password
