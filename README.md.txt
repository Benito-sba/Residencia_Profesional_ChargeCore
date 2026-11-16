# ChargeCore - CRODE Mérida

## ¿Qué es el programa?

ChargeCore es un sistema de gestión centralizada de estaciones de carga para
vehículos eléctricos. Permite al personal administrador y operativo supervisar
cargadores, usuarios, sesiones de carga y la información recibida de los equipos.
El proyecto se desarrolló como residencia profesional para CRODE Mérida.

## Necesidad y objetivo

La operación de varios cargadores requiere un lugar común para conocer si están
conectados, revisar su estado y consultar las cargas y mediciones registradas.
ChargeCore atiende esa necesidad al concentrar la operación en una plataforma:
facilita la administración de cargadores y usuarios, el seguimiento de sesiones
y la consulta de datos operativos sin depender de revisar cada equipo por separado.

## Funciones principales

- Registrar y administrar cargadores y sus conectores.
- Consultar conexiones, estados, mediciones y eventos de los cargadores.
- Administrar usuarios, empresas, vehículos y tarjetas NFC.
- Consultar sesiones de carga, historial, reportes y recompensas.
- Enviar a los cargadores los comandos remotos implementados para su versión OCPP.

## Lenguajes y tecnologías

| Componente | Tecnología |
|---|---|
| Lenguaje principal | Python 3.12 |
| Backend y API | FastAPI, Pydantic |
| Interfaz web | NiceGUI |
| Cliente de la API desde la interfaz | Requests |
| Comunicación con cargadores | OCPP sobre JSON y WebSocket |
| Acceso a datos | Motor, controlador asíncrono de MongoDB |
| Base de datos | MongoDB Atlas |

## APIs y comunicación

El backend expone una API REST HTTP con respuestas JSON para las funciones de
la plataforma, como autenticación, cargadores, usuarios, sesiones, vehículos,
tarjetas NFC, reportes y configuración. La interfaz NiceGUI consume esta API
desde Python mediante Requests.

Los cargadores se comunican directamente con el backend por WebSocket en la ruta
`/ocpp/{charge_point_id}`. Esta conexión usa mensajes OCPP en formato JSON y es
independiente de la API REST usada por la interfaz.

## Protocolos OCPP

ChargeCore implementa adaptadores para:

- **OCPP 1.6J**: acepta los subprotocolos WebSocket `ocpp1.6` y `ocpp1.6j`.
- **OCPP 2.0.1**: subprotocolo `ocpp2.0.1`.
- **OCPP 2.1**: subprotocolo `ocpp2.1`.

Las versiones disponibles se pueden habilitar desde la configuración del
sistema. La conexión negocia una versión habilitada que también anuncie el
cargador. La compatibilidad funcional depende de los mensajes y comandos
implementados por cada adaptador; no todas las funciones definidas por OCPP
están necesariamente disponibles. OCPP 1.6 SOAP no está soportado.

## Base de datos

La base de datos activa es **MongoDB Atlas**. El backend se conecta de forma
asíncrona mediante Motor y almacena la información operativa del sistema, como
usuarios, empresas, cargadores, sesiones, mediciones, tarjetas NFC y recompensas.
La aplicación requiere una URI de conexión de MongoDB Atlas; no utiliza Supabase
ni una base MongoDB local como alternativa.
