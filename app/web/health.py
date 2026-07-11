from flask import jsonify

from ..config import PANEL_HEALTH_REQUIRES_XRAY
from .core import route, state


@route("/healthz", methods=["GET"])
def healthz():
    state.sync_traffic_state()
    data_plane_running = state.data_plane_running()
    ai_node_running = state.ai_node_running()
    healthy = data_plane_running if PANEL_HEALTH_REQUIRES_XRAY else True
    status_code = 200 if healthy else 500
    return jsonify({
        "ok": healthy,
        "data_plane_running": data_plane_running,
        "ai_node_running": ai_node_running,
    }), status_code
