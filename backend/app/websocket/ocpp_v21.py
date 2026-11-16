from ocpp.v21 import call_result
from ocpp.v21 import ChargePoint as OCPP21ChargePoint

from app.websocket.ocpp_v201 import OCPP2xChargePointHandler


class OCPP21ChargePointHandler(OCPP2xChargePointHandler, OCPP21ChargePoint):
    call_result_module = call_result
    negotiated_subprotocol = "ocpp2.1"
