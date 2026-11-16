from datetime import datetime, timedelta
from typing import Optional
from bson import ObjectId
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from app.config import settings
from app.database import get_collection

COMPANY_ROLES = {"dueno_empresa", "cliente"}

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
    user = await _require_roles(token, {"cliente"})
    await _get_company_for_user(user)
    return user


async def require_superadmin(token: str = Depends(oauth2_scheme)):
    user = await _require_roles(token, {"superadmin"})
    _validate_superadmin_company(user)
    return user


def _validate_superadmin_company(user: dict) -> None:
    if user.get("empresa_id") is not None:
        raise HTTPException(
            status_code=403,
            detail="El superadministrador no puede pertenecer a una empresa",
        )


async def _get_company_for_user(user: dict):
    if user.get("rol") not in COMPANY_ROLES:
        raise HTTPException(status_code=403, detail="El usuario no pertenece a una empresa")

    company_id = user.get("empresa_id")
    if not isinstance(company_id, ObjectId):
        raise HTTPException(status_code=403, detail="El usuario no tiene una empresa válida")

    company = await get_collection("empresas").find_one({"_id": company_id})
    if (
        company is None
        or company.get("activo") is not True
        or company.get("estado") == "suspendida"
    ):
        raise HTTPException(status_code=403, detail="La empresa no está disponible")
    return company


async def _require_company_owner_user(user: dict):
    if user.get("rol") != "dueno_empresa":
        raise HTTPException(status_code=403, detail="Permisos insuficientes")
    await _get_company_for_user(user)
    return user


async def get_current_company(current_user=Depends(get_current_user)):
    return await _get_company_for_user(current_user)


async def require_company_owner(token: str = Depends(oauth2_scheme)):
    return await _require_company_owner_user(
        await get_current_user(token)
    )

async def require_company_admin_access(token: str = Depends(oauth2_scheme)):
    user = await get_current_user(token)
    if user.get("rol") == "superadmin":
        _validate_superadmin_company(user)
    elif user.get("rol") == "dueno_empresa":
        await _require_company_owner_user(user)
    else:
        raise HTTPException(status_code=403, detail="Permisos insuficientes")
    return user


async def require_company_member(token: str = Depends(oauth2_scheme)):
    user = await get_current_user(token)
    if user.get("rol") == "superadmin":
        _validate_superadmin_company(user)
    elif user.get("rol") in COMPANY_ROLES:
        await _get_company_for_user(user)
    else:
        raise HTTPException(status_code=403, detail="Permisos insuficientes")
    return user


def company_scoped_query(current_user: dict, query: dict | None = None) -> dict:
    scoped_query = dict(query or {})
    role = current_user.get("rol")
    if role == "superadmin":
        _validate_superadmin_company(current_user)
        return scoped_query
    if role in COMPANY_ROLES and isinstance(current_user.get("empresa_id"), ObjectId):
        scoped_query["empresa_id"] = current_user["empresa_id"]
        return scoped_query
    raise HTTPException(status_code=403, detail="La empresa del usuario no es válida")


async def require_user_management_access(token: str = Depends(oauth2_scheme)):
    user = await get_current_user(token)
    role = user.get("rol")
    if role == "superadmin":
        _validate_superadmin_company(user)
    elif role == "dueno_empresa":
        await _require_company_owner_user(user)
    else:
        raise HTTPException(status_code=403, detail="Permisos insuficientes")
    return user


def validate_legacy_user_assignment(role: str, company_id=None) -> None:
    if role not in {"admin", "operador", "consulta", "cliente"} or company_id is not None:
        raise HTTPException(
            status_code=403,
            detail="La asignación de nuevos roles o empresas requiere el flujo autorizado",
        )