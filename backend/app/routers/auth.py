from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.security import OAuth2PasswordRequestForm
from app.database import get_collection
from app.auth import verify_password, create_access_token, get_password_hash, require_admin
from app.models.user import UsuarioCreate

router = APIRouter(prefix="/api/auth", tags=["Autenticación"])

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

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(user: UsuarioCreate, current_user=Depends(require_admin)):
    existing = await get_collection("usuarios").find_one({"email": user.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email ya registrado")

    user_dict = user.model_dump()
    user_dict["password"] = get_password_hash(user_dict["password"])

    result = await get_collection("usuarios").insert_one(user_dict)
    return {"id": str(result.inserted_id), "mensaje": "Usuario registrado"}