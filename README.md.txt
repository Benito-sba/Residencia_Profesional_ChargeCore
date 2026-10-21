# CSMS CRODE Mérida

**Central System Management System** para el control de estaciones de carga de vehículos eléctricos basado en **OCPP 1.6J**.

Proyecto de residencia profesional para CRODE Mérida.

---

## Tecnologías

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.12, FastAPI, Motor (MongoDB Async) |
| Frontend | Python 3.12, Streamlit |
| Base de datos activa | MongoDB 6.0+ (Motor) |
| Base de datos preparada | Supabase / PostgreSQL |
| Protocolo | OCPP 1.6J sobre WebSockets |
| Seguridad | JWT + Passlib (bcrypt) |
| Validación | Pydantic v2 |

## Compatibilidad de cargadores

ChargeCore no restringe el fabricante ni el modelo: los registra desde el
`BootNotification` del equipo. La compatibilidad de comunicación requiere que
el cargador implemente OCPP 1.6J por JSON/WebSocket, se registre en ChargeCore
y pueda configurarse con la URL y credenciales OCPP correspondientes. Los
tipos de conector se pueden capturar como texto para incluir estándares no
predefinidos.

Se reciben y registran los avisos estándar de firmware y diagnóstico. Los
mensajes `DataTransfer` propietarios se reconocen, pero ChargeCore responde
`UnknownVendorId`; por tanto, las funciones exclusivas de un fabricante no se
ejecutan. SOAP, OCPP 2.0.1 y cargadores que no implementen OCPP 1.6J requieren
una integración distinta.

---


##  Estructura del Proyecto

csms_crode/
├── backend/          # API REST + Servidor OCPP
│   ├── app/
│   │   ├── main.py
│   │   ├── database.py
│   │   ├── auth.py
│   │   ├── models/
│   │   ├── routers/
│   │   ├── services/
│   │   └── websocket/
│   └── requirements.txt
├── frontend/         # Interfaz Streamlit
│   ├── app.py
│   ├── pages/
│   ├── components/
│   ├── utils/
│   └── requirements.txt
└── README.md


---

## Instalación

### Requisitos
- Python 3.12
- MongoDB corriendo en `localhost:27017`

### Backend

```bash
cd backend
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python init_db.py      # Crea índices y solicita la contraseña del admin inicial
python start.py        # Arranca en http://localhost:8000
```

`start.py` es el lanzador local de desarrollo y habilita recarga automática. Para
ejecutar el backend en un servidor, desde `backend` use Uvicorn sin recarga:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-reload
```

Al probar OCPP, la consola del backend registra los intentos de conexión y el
motivo de cada rechazo (por ejemplo, cargador no registrado, credenciales
incorrectas o subprotocolo incompatible). No registra la contraseña. Si no
aparece ningún intento al reconectar, el cargador no está alcanzando la ruta
WebSocket del backend.

El lanzador local `backend/start.py` también muestra en terminal los detalles
del handshake WebSocket (IP cliente, ruta, subprotocolos ofrecidos y resultado),
los errores con su traceback y el tipo/acción de cada trama OCPP. Los registros
diagnósticos nuevos no escriben archivos ni guardan encabezados de autorización
o payloads OCPP en la terminal; la persistencia operativa de mensajes OCPP en
MongoDB sigue funcionando para la pantalla de monitoreo.

Configure `SECRET_KEY`, `MONGODB_URL`, `DATABASE_NAME`, `DEBUG` y
`OCPP_COMMAND_TIMEOUT_SECONDS` en el entorno del servicio. La URL de MongoDB
debe permanecer en una red privada; no use su valor local predeterminado en
producción.

### Estado de Supabase

MongoDB es la base operativa del sistema. Supabase no contiene las sesiones,
tarjetas NFC, clientes ni recompensas de esta versión. No configure credenciales
de Supabase ni ejecute un esquema para publicar esta versión: el archivo
`backend/supabase/schema.sql` no está incluido y la migración no está terminada.

### Configuración de seguridad del backend (Windows)

`SECRET_KEY` es obligatoria y debe tener al menos 32 caracteres. Genere una clave
aleatoria y guárdela en las variables de usuario de Windows, sin incluirla en el
repositorio. En PowerShell puede hacerlo sin imprimirla:

```powershell
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$bytes = New-Object byte[] 48
$rng.GetBytes($bytes)
$secret = [Convert]::ToBase64String($bytes)
[Environment]::SetEnvironmentVariable("SECRET_KEY", $secret, "User")
$env:SECRET_KEY = $secret
$rng.Dispose()
Remove-Variable secret, bytes, rng
```

El valor queda disponible para procesos nuevos del usuario; la asignación a
`$env:SECRET_KEY` también lo hace disponible en la terminal actual. Reinicie
manualmente las terminales o procesos que ya estaban abiertos para que hereden
la configuración. No comparta ni registre el valor.

Con `DEBUG=false`, la aplicación exige una URI MongoDB con TLS (`mongodb+srv`
habilita TLS o use `tls=true`) y un replica set o clúster compatible con
transacciones. La aplicación guarda el estado de sesiones y lecturas con
transacciones; una instancia MongoDB standalone no sirve para producción.
`/health` comprueba que MongoDB responda a `ping`.

Ejecute `python init_db.py` manualmente solo cuando MongoDB esté autorizado y
disponible. El inicializador conserva su creación de índices y solicita la
contraseña y confirmación ocultas únicamente si aún no existe un administrador;
no cambia administradores existentes. `INITIAL_ADMIN_EMAIL` permite indicar
el correo del primer administrador; si no se define, se conserva
`admin@crode.merida`.

El frontend Streamlit actual llama a la API mediante `requests` desde Python,
por lo que no necesita CORS del navegador. `CORS_ORIGINS` puede permanecer
vacía; si en el futuro un navegador llama directamente a la API, configure
únicamente sus orígenes HTTP(S) exactos, separados por comas. No use `*`.

------------------

# Iniciar Streamlit
streamlit run app.py

El frontend usa `http://localhost:8000/api` de forma predeterminada. Para
publicarlo separado del backend, configure `CHARGECORE_API_URL` antes de iniciar
Streamlit con la URL pública HTTPS de la API, incluyendo `/api`, por ejemplo:

```powershell
$env:CHARGECORE_API_URL = "https://api.example.com/api"
streamlit run app.py
```

El valor se consume desde el proceso de Streamlit y no desde el navegador. Para
publicar el backend también se requiere configurar `SECRET_KEY`,
`MONGODB_URL`, `DATABASE_NAME` y `CORS_ORIGINS` mediante el entorno elegido.
No exponga MongoDB públicamente. La conexión OCPP desde una red externa requiere
un endpoint `wss://` con TLS y soporte de WebSocket en el proxy/hosting; esos
elementos dependen de la infraestructura seleccionada y no se configuran aquí.

### Publicar en Render para probar un cargador

El archivo `render.yaml` define dos servicios web: la API OCPP/FastAPI y la
interfaz Streamlit. Render enlaza la URL pública del backend con el frontend;
este último agrega `/api` automáticamente. El backend usa un solo worker porque
el registro de conexiones WebSocket del cargador vive en memoria del proceso.
No escale horizontalmente ni inicie workers adicionales sin mover ese estado a
un mecanismo compartido.

El frontend requiere versiones compatibles de NumPy, pandas y PyArrow, que
están fijadas en `frontend/requirements.txt`. Si Render muestra errores como
`pyarrow requires NumPy 2.0 or newer` o indica que falta PyArrow, publica el
cambio más reciente del repositorio y vuelve a desplegar el servicio
`chargecore-frontend`; Render instala estas dependencias durante su compilación.

1. Sube el proyecto a un repositorio Git y, en Render, crea un **Blueprint**
   desde ese repositorio usando `render.yaml`.
2. Cuando Render lo solicite, proporciona `MONGODB_URL` con la URI de una
   instancia MongoDB accesible desde Render. Configura la lista de IP de acceso
   de MongoDB según las direcciones salientes del plan de Render; no abras la
   base de datos a todo Internet. Usa TLS y confirma que el clúster sea replica
   set o compatible con transacciones.
3. Espera a que ambos servicios terminen de desplegarse y confirma que
   `https://<backend>.onrender.com/health` responde
   `{"status":"healthy"}`. Este endpoint requiere conectividad con MongoDB. La
   URL del frontend será el dominio público asignado por Render.
4. Configura `INITIAL_ADMIN_EMAIL=nitoacevedo46@gmail.com` en el entorno del
   backend antes de inicializar la base. Ejecuta `python init_db.py` una sola
   vez desde la carpeta `backend`, con `MONGODB_URL` apuntando a la misma base
   configurada en Render y `SECRET_KEY` igual a la del backend. Puedes hacerlo
   desde Render Shell si tu plan lo permite o desde tu PC si tiene acceso a
   MongoDB. El script solicita una contraseña nueva y su confirmación sin
   mostrarlas; guárdala de forma segura. Si se ejecuta en un Render Shell donde
   la entrada interactiva no está disponible, ejecútalo localmente contra Atlas
   sin colocar la URI en el repositorio ni compartirla. El script no reemplaza
   administradores existentes.
5. Inicia sesión en la interfaz con ese administrador y crea
   `benitobalam03@gmail.com` desde **Usuarios** con rol **Cliente** y una
   contraseña nueva. No se debe crear la cuenta cliente como administrador ni
   reutilizar contraseñas temporales de la base local.
6. Registra el cargador como activo en la interfaz. Configura en el equipo el
   endpoint OCPP `wss://<backend>.onrender.com/ocpp/<charge_point_id>` y el
   subprotocolo `ocpp1.6j` (OCPP 1.6J sobre JSON/WebSocket). El
   `charge_point_id` debe coincidir exactamente con el registrado.
7. Inicia sesión con el administrador y revisa **Cargadores** y **Eventos
   OCPP**. Deben aparecer la conexión, `BootNotification`, `Heartbeat`, estados
   de conectores y las mediciones `MeterValues` que envíe el equipo. En
   **Cargadores**, la actualización automática de la pantalla se puede activar
   o desactivar desde el control de su encabezado.

Render acepta conexiones WebSocket públicas; para tráfico externo se debe usar
`wss://`. Los servicios gratuitos pueden suspenderse por inactividad; para una
prueba física continua el servicio backend debe permanecer despierto. Valida
primero únicamente la comunicación y telemetría antes de ejecutar comandos que
inicien una carga real. Estas instrucciones preparan el despliegue, pero no lo
publican automáticamente: hace falta conectar el repositorio en tu cuenta
Render y configurar allí las credenciales de MongoDB.

Antes de actualizar una base existente, revisa y resuelve sesiones activas
duplicadas por cliente y recompensas duplicadas para la misma combinación de
cliente, regla e hito. Los índices únicos nuevos rechazan esos duplicados y el
backend no iniciará si no puede crearlos; no se borran registros
automáticamente.

Las recompensas se seleccionan en la pantalla **Carga Activa** y se marcan como
usadas al confirmarse el cierre OCPP. El canje queda asociado al historial de
la sesión, pero esta versión no calcula ni modifica importes de cobro. Las
bajas de clientes y tarjetas conservan los registros históricos y desactivan
las cuentas o bloquean las tarjetas.

Las reglas activas se evalúan contra el total de cargas finalizadas. Si varios
cierres concurrentes hacen que se rebase un hito, se emiten los hitos activos
que aún no se habían otorgado en el siguiente cierre. Una recompensa ya emitida
conserva sus datos de catálogo y regla en su copia histórica; editar o desactivar
la regla no modifica esa recompensa.

El despliegue no verifica los respaldos ni restaura datos automáticamente.
Configura respaldos administrados en el proveedor MongoDB y prueba una
restauración antes de abrir el sistema a clientes. También prueba en staging el
flujo NFC y de carga con el cargador o un simulador OCPP.

El backend solo acepta el subprotocolo OCPP `ocpp1.6j`. Para autenticar, el
cargador debe estar registrado y activo; el usuario Basic Auth debe coincidir
con su `charge_point_id` y la contraseña debe coincidir con la configurada en
ChargeCore. OCPP 1.6 SOAP y el subprotocolo `ocpp1.6` sin `j` no son compatibles.