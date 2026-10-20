from datetime import datetime, timedelta
from typing import Optional
from bson import ObjectId
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.config import settings
from app.database import get_collection

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(
        to_encode,
        settings.SECRET_KEY.get_secret_value(),
        algorithm=settings.ALGORITHM,
    )

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[settings.ALGORITHM],
        )
        user_id: str = payload.get("sub")
        if not isinstance(user_id, str) or not ObjectId.is_valid(user_id):
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = await get_collection("usuarios").find_one({"_id": ObjectId(user_id)})
    if user is None or not user.get("activo", True):
        raise credentials_exception
    return user

async def get_current_admin(token: str = Depends(oauth2_scheme)):
    user = await get_current_user(token)
    if user.get("rol") not in ["admin", "operador"]:
        raise HTTPException(status_code=403, detail="Permisos insuficientes")
    return user

async def _require_roles(token: str, allowed_roles: set[str]):
    user = await get_current_user(token)
    if user.get("rol") not in allowed_roles:
        raise HTTPException(status_code=403, detail="Permisos insuficientes")
    return user

async def require_admin(token: str = Depends(oauth2_scheme)):
    return await _require_roles(token, {"admin"})

async def require_operator(token: str = Depends(oauth2_scheme)):
    return await _require_roles(token, {"admin", "operador"})

async def require_read_access(token: str = Depends(oauth2_scheme)):
    return await _require_roles(token, {"admin", "operador", "consulta"})

async def require_client(token: str = Depends(oauth2_scheme)):
    return await _require_roles(token, {"cliente"})