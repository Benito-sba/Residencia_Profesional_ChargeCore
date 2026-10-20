from fastapi import APIRouter, Depends
from datetime import datetime, timedelta
from app.database import get_collection
from app.auth import require_read_access

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])

@router.get("/kpi")
async def get_kpi(current_user=Depends(require_read_access)):
    sesiones_col = get_collection("sesiones")
    cargadores_col = get_collection("cargadores")
    usuarios_col = get_collection("usuarios")
    vehiculos_col = get_collection("vehiculos")
    recompensas_col = get_collection("recompensas")
    logs_col = get_collection("logs")
    eventos_col = get_collection("eventos_ocpp")

    total_cargas = await sesiones_col.count_documents({"estado": "finalizada"})
    cargas_hoy = await sesiones_col.count_documents({
        "estado": "finalizada",
        "fecha_inicio": {"$gte": datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)}
    })

    pipeline = [{"$match": {"estado": "finalizada"}}, {"$group": {"_id": None, "total": {"$sum": "$energia_consumida_kwh"}}}]
    energia_result = await sesiones_col.aggregate(pipeline).to_list(1)
    energia_total = energia_result[0]["total"] if energia_result else 0

    cargadores_activos = await cargadores_col.count_documents({"activo": True})
    cargadores_conectados = await cargadores_col.count_documents({"activo": True, "conectado": True})
    cargadores_ocupados = await cargadores_col.count_documents({"estado_general": "ocupado"})
    cargadores_mantenimiento = await cargadores_col.count_documents({"estado_general": "mantenimiento"})
    cargadores_fuera = await cargadores_col.count_documents({"estado_general": "fuera_de_servicio"})

    total_usuarios = await usuarios_col.count_documents({})
    total_vehiculos = await vehiculos_col.count_documents({})

    recompensas_pendientes = await recompensas_col.count_documents({"usado": False})
    sesiones_activas = await sesiones_col.count_documents({"estado": {"$in": ["iniciada", "activa"]}})

    hace_24h = datetime.utcnow() - timedelta(hours=24)
    alertas_recientes = await logs_col.count_documents({"timestamp": {"$gte": hace_24h}, "nivel": {"$in": ["warning", "error"]}})
    eventos_recientes = await eventos_col.count_documents({"timestamp": {"$gte": hace_24h}})

    return {
        "total_cargas": total_cargas,
        "cargas_hoy": cargas_hoy,
        "energia_total_kwh": round(energia_total, 2),
        "cargadores_activos": cargadores_activos,
        "cargadores_conectados": cargadores_conectados,
        "cargadores_ocupados": cargadores_ocupados,
        "cargadores_mantenimiento": cargadores_mantenimiento,
        "cargadores_fuera_de_servicio": cargadores_fuera,
        "total_usuarios": total_usuarios,
        "total_vehiculos": total_vehiculos,
        "recompensas_pendientes": recompensas_pendientes,
        "sesiones_activas": sesiones_activas,
        "alertas_recientes": alertas_recientes,
        "eventos_recientes": eventos_recientes
    }

@router.get("/grafica-consumo-semanal")
async def get_weekly_consumption(current_user=Depends(require_read_access)):
    sesiones_col = get_collection("sesiones")
    hace_7_dias = datetime.utcnow() - timedelta(days=7)

    pipeline = [
        {"$match": {"estado": "finalizada", "fecha_inicio": {"$gte": hace_7_dias}}},
        {"$group": {
            "_id": {"year": {"$year": "$fecha_inicio"}, "month": {"$month": "$fecha_inicio"}, "day": {"$dayOfMonth": "$fecha_inicio"}},
            "energia": {"$sum": "$energia_consumida_kwh"},
            "cargas": {"$sum": 1}
        }},
        {"$sort": {"_id.year": 1, "_id.month": 1, "_id.day": 1}}
    ]

    result = await sesiones_col.aggregate(pipeline).to_list(30)
    data = []
    for r in result:
        fecha = f"{r['_id']['year']}-{r['_id']['month']:02d}-{r['_id']['day']:02d}"
        data.append({"fecha": fecha, "energia_kwh": round(r["energia"], 2), "num_cargas": r["cargas"]})
    return data

@router.get("/grafica-consumo-diario")
async def get_daily_consumption(current_user=Depends(require_read_access)):
    return await get_weekly_consumption(current_user)

@router.get("/sesiones-por-cargador")
async def get_sessions_by_charger(current_user=Depends(require_read_access)):
    pipeline = [{"$match": {"estado": "finalizada"}}, {"$group": {
        "_id": "$charge_point_id", "sesiones": {"$sum": 1}
    }}, {"$sort": {"sesiones": -1}}]
    return [{"charge_point_id": item["_id"], "sesiones": item["sesiones"]}
            async for item in get_collection("sesiones").aggregate(pipeline)]

@router.get("/energia-por-cargador")
async def get_energy_by_charger(current_user=Depends(require_read_access)):
    pipeline = [{"$match": {"estado": "finalizada"}}, {"$group": {
        "_id": "$charge_point_id", "energia_kwh": {"$sum": "$energia_consumida_kwh"}
    }}, {"$sort": {"energia_kwh": -1}}]
    return [{"charge_point_id": item["_id"], "energia_kwh": round(item["energia_kwh"], 3)}
            async for item in get_collection("sesiones").aggregate(pipeline)]

@router.get("/tendencia-uso")
async def get_usage_trend(current_user=Depends(require_read_access)):
    now = datetime.utcnow()
    start_date = now.date() - timedelta(days=29)
    start_datetime = datetime.combine(start_date, datetime.min.time())
    dates = [(start_date + timedelta(days=offset)).isoformat() for offset in range(30)]

    stations = {
        charger["charge_point_id"]: {}
        async for charger in get_collection("cargadores").find(
            {"charge_point_id": {"$exists": True, "$ne": None}},
            {"charge_point_id": 1},
        )
    }
    duration_minutes = {
        "$ifNull": [
            "$duracion_minutos",
            {
                "$divide": [
                    {
                        "$subtract": [
                            {"$ifNull": ["$fecha_fin", "$fecha_inicio"]},
                            "$fecha_inicio",
                        ]
                    },
                    60000,
                ]
            },
        ]
    }
    pipeline = [
        {"$match": {
            "estado": "finalizada",
            "fecha_inicio": {"$gte": start_datetime, "$lte": now},
            "charge_point_id": {"$exists": True, "$ne": None},
        }},
        {"$group": {
            "_id": {
                "charge_point_id": "$charge_point_id",
                "year": {"$year": "$fecha_inicio"},
                "month": {"$month": "$fecha_inicio"},
                "day": {"$dayOfMonth": "$fecha_inicio"},
            },
            "sesiones": {"$sum": 1},
            "energia_kwh": {"$sum": {"$ifNull": ["$energia_consumida_kwh", 0]}},
            "duracion_minutos": {"$sum": duration_minutes},
        }},
        {"$sort": {
            "_id.year": 1,
            "_id.month": 1,
            "_id.day": 1,
            "_id.charge_point_id": 1,
        }},
    ]

    result = await get_collection("sesiones").aggregate(pipeline).to_list(None)
    for item in result:
        key = item["_id"]
        charge_point_id = key["charge_point_id"]
        date = f"{key['year']}-{key['month']:02d}-{key['day']:02d}"
        stations.setdefault(charge_point_id, {})[date] = {
            "sesiones": item["sesiones"],
            "energia_kwh": item["energia_kwh"],
            "horas_uso": item["duracion_minutos"] / 60,
        }

    station_series = []
    for charge_point_id, daily_data in stations.items():
        daily = [daily_data.get(date, {}) for date in dates]
        station_series.append({
            "charge_point_id": charge_point_id,
            "total_sesiones": sum(item.get("sesiones", 0) for item in daily),
            "sesiones": [item.get("sesiones", 0) for item in daily],
            "energia_kwh": [round(item.get("energia_kwh", 0), 2) for item in daily],
            "horas_uso": [round(item.get("horas_uso", 0), 2) for item in daily],
        })

    return {"dates": dates, "stations": station_series}

@router.get("/alertas-recientes")
async def get_recent_alerts(limit: int = 5, current_user=Depends(require_read_access)):
    logs_col = get_collection("logs")
    cursor = logs_col.find({"nivel": {"$in": ["warning", "error"]}}).sort("timestamp", -1).limit(limit)
    alerts = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        alerts.append(doc)
    return alerts

@router.get("/eventos-ocpp-recientes")
async def get_recent_events(limit: int = 5, current_user=Depends(require_read_access)):
    eventos_col = get_collection("eventos_ocpp")
    cursor = eventos_col.find().sort("timestamp", -1).limit(limit)
    events = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        events.append(doc)
    return events

@router.get("/recompensas-pendientes-resumen")
async def get_pending_rewards_summary(limit: int = 5, current_user=Depends(require_read_access)):
    recompensas_col = get_collection("recompensas")
    usuarios_col = get_collection("usuarios")
    cursor = recompensas_col.find({"usado": False}).sort("fecha_otorgamiento", -1).limit(limit)
    rewards = []
    async for r in cursor:
        r["_id"] = str(r["_id"])
        user = await usuarios_col.find_one({"_id": r.get("usuario_id")})
        r["usuario_nombre"] = user["nombre"] if user else "Desconocido"
        rewards.append(r)
    return rewards