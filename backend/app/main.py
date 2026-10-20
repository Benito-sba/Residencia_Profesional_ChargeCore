import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from pymongo.errors import PyMongoError

from app.config import settings
from app.database import connect_db, close_db, db
from app.supabase_database import connect_supabase
from app.routers import auth, users, vehicles, nfc_cards, chargers, sessions, rewards, reports, dashboard, monitoring, client_portal
from app.websocket.diagnostics import OCPPWebSocketDiagnosticsMiddleware
from app.websocket.ocpp_server import ocpp_endpoint

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await connect_db()
        connect_supabase()
        yield
    finally:
        await close_db()

app = FastAPI(
    title="ChargeCore - API OCPP 1.6J",
    description="Plataforma para el Control y Monitoreo de Sistemas de Carga de Vehiculos Electricos",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(OCPPWebSocketDiagnosticsMiddleware)

# Routers HTTP REST
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(vehicles.router)
app.include_router(nfc_cards.router)
app.include_router(chargers.router)
app.include_router(sessions.router)
app.include_router(rewards.router)
app.include_router(reports.router)
app.include_router(dashboard.router)
app.include_router(monitoring.router)
app.include_router(client_portal.router)

# Ruta WebSocket OCPP para los cargadores
@app.websocket("/ocpp/{charge_point_id}")
async def websocket_ocpp(websocket: WebSocket, charge_point_id: str):
    await ocpp_endpoint(websocket, charge_point_id)

@app.get("/")
async def root():
    return {
        "message": "ChargeCore - API OCPP 1.6J",
        "version": "1.0.0",
        "status": "operativo"
    }

@app.get("/health")
async def health_check():
    if db.client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Base de datos no disponible",
        )
    try:
        await db.client.admin.command("ping")
    except PyMongoError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Base de datos no disponible",
        ) from error
    return {"status": "healthy"}