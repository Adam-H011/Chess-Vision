import http.client
import json

import chess

from chessvision.browser_bridge import BrowserBridge, MAX_BODY


def request(bridge, body, token=None, origin="chrome-extension://test"):
    conn = http.client.HTTPConnection("127.0.0.1", bridge.status.port)
    conn.request("POST", "/", body=body, headers={"Origin": origin, "Authorization": "Bearer " + (token or bridge.token), "Content-Type": "application/json"})
    response = conn.getresponse(); response.read(); return response.status


def test_bridge_accepts_valid_position_and_rejects_token_origin_and_size():
    bridge = BrowserBridge(); bridge.start()
    try:
        body = json.dumps({"placement": chess.STARTING_BOARD_FEN, "metadata": {}})
        assert request(bridge, body) == 202
        assert bridge.get_position().placement == chess.STARTING_BOARD_FEN
        assert request(bridge, body, token="bad") == 401
        assert request(bridge, body, origin="https://evil.example") == 403
        assert request(bridge, "x" * (MAX_BODY + 1)) == 413
        assert request(bridge, "not json") == 400
    finally:
        bridge.close()
