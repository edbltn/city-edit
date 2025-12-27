import json
import time
from flask import Flask
from flask_sock import Sock

app = Flask(__name__)
sock = Sock(app)

def make_state(rev: int):
    # A tiny animated line: shifts east-west over time
    shift = (rev % 50) * 0.0002
    return {
        "revision": rev,
        "overlays": {
            "moving_line": {
                "type": "geojson",
                "data": {
                    "type": "FeatureCollection",
                    "features": [{
                        "type": "Feature",
                        "properties": {"rev": rev},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [-73.98 + shift, 40.77],
                                [-73.97 + shift, 40.78],
                                [-73.96 + shift, 40.79],
                            ]
                        }
                    }]
                }
            }
        }
    }

@sock.route("/ws")
def ws(ws):
    rev = 1
    while True:
        msg = {"type": "map_state", "state": make_state(rev)}
        ws.send(json.dumps(msg))
        rev += 1
        time.sleep(1)  # update frequency

if __name__ == "__main__":
    # Run: python app.py
    app.run(host="0.0.0.0", port=5001, debug=False)

