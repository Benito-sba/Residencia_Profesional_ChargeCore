import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from app.database import get_collection
from app.auth import (
    create_access_token,
    get_current_user,
    get_password_hash,
    require_admin,
    require_superadmin,
    validate_legacy_user_assignment,
    verify_password,
)
from app.config import settings
from app.models.user import UsuarioCreate

router = APIRouter(prefix="/api/auth", tags=["Autenticación"])


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetRecipientUpdate(BaseModel):
    email: EmailStr


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


PASSWORD_RESET_MESSAGE = (
    "Si la cuenta existe, se enviará una solicitud de restablecimiento "
    "al administrador."
)


def _send_password_reset_notice(
    recipient_email: str,
    user_name: str,
    user_email: str,
) -> None:
    message = EmailMessage()
    message["Subject"] = "Solicitud de restablecimiento de contraseña"
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = recipient_email
    message.set_content(
        "Se solicitó restablecer la contraseña de una cuenta de ChargeCore.\n\n"
        f"Nombre: {user_name}\n"
        f"Correo de la cuenta: {user_email}\n\n"
        "Contacta al usuario para verificar su identidad y ayudarle a "
        "restablecer su contraseña. No respondas a este correo con contraseñas."
    )

    if settings.SMTP_USE_SSL:
        smtp_connection = smtplib.SMTP_SSL(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            timeout=10,
            context=ssl.create_default_context(),
        )
    else:
        smtp_connection = smtplib.SMTP(
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            timeout=10,
        )

    with smtp_connection as server:
        if settings.SMTP_STARTTLS and not settings.SMTP_USE_SSL:
            server.starttls()
        if settings.SMTP_USERNAME:
            password = settings.SMTP_PASSWORD
            server.login(
                settings.SMTP_USERNAME,
                password.get_secret_value() if password else "",
            )
        server.send_message(message)


@router.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = await get_collection("usuarios").find_one({"email": form_data.username})
    if not user or not verify_password(form_data.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.get("activo", True):
        raise HTTPException(status_code=403, detail="Usuario desactivado")

    token = create_access_token({"sub": str(user["_id"]), "rol": user.get("rol", "usuario")})
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": str(user["_id"]),
            "nombre": user["nombre"],
            "email": user["email"],
            "rol": user.get("rol", "usuario")
        }
    }


@router.post("/password-reset-request")
async def password_reset_request(request: PasswordResetRequest):
    if not settings.smtp_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El envío de correo no está configurado en el servidor.",
        )

    recipient_settings = await get_collection("system_settings").find_one(
        {"_id": "password_reset"}
    )
    recipient_email = (
        recipient_settings.get("recipient_email")
        if recipient_settings
        else None
    )
    if not recipient_email:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se ha configurado el correo receptor de solicitudes.",
        )

    user = await get_collection("usuarios").find_one({"email": request.email})
    if not user or not user.get("activo", True):
        return {"mensaje": PASSWORD_RESET_MESSAGE}

    try:
        await asyncio.to_thread(
            _send_password_reset_notice,
            recipient_email,
            user.get("nombre", ""),
            user["email"],
        )
    except (OSError, smtplib.SMTPException) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No fue posible enviar la solicitud de restablecimiento.",
        ) from error

    return {"mensaje": PASSWORD_RESET_MESSAGE}


@router.get("/password-reset-recipient")
async def get_password_reset_recipient(current_user=Depends(require_superadmin)):
    settings_document = await get_collection("system_settings").find_one(
        {"_id": "password_reset"}
    )
    return {
        "email": (
            settings_document.get("recipient_email")
            if settings_document
            else None
        )
    }


@router.put("/password-reset-recipient")
async def update_password_reset_recipient(
    request: PasswordResetRecipientUpdate,
    current_user=Depends(require_superadmin),
):
    await get_collection("system_settings").update_one(
        {"_id": "password_reset"},
        {"$set": {"recipient_email": str(request.email)}},
        upsert=True,
    )
    return {"mensaje": "Correo receptor actualizado"}


@router.post("/change-password")
async def change_password(
    request: PasswordChangeRequest,
    current_user=Depends(get_current_user),
):
    if not verify_password(request.current_password, current_user["password"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña actual es incorrecta.",
        )

    await get_collection("usuarios").update_one(
        {"_id": current_user["_id"]},
        {"$set": {"password": get_password_hash(request.new_password)}},
    )
    return {"mensaje": "Contraseña actualizada correctamente"}


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(user: UsuarioCreate, current_user=Depends(require_admin)):
    validate_legacy_user_assignment(user.rol, user.empresa_id)
    existing = await get_collection("usuarios").find_one({"email": user.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email ya registrado")

    user_dict = user.model_dump()
    user_dict["password"] = get_password_hash(user_dict["password"])

    result = await get_collection("usuarios").insert_one(user_dict)
    return {"id": str(result.inserted_id), "mensaje": "Usuario registrado"}