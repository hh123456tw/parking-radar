"""步行路線服務介面；實作將以 OpenRouteService Matrix API 為邊界。"""

import requests

ORS_WALKING_MATRIX_URL = \
    "https://api.heigit.org/openrouteservice/v2/matrix/foot-walking"


class WalkingRouteError(RuntimeError):
    """表示步行路線服務暫時不可用，可安全退回直線距離。"""


def fetch_walking_routes(rows, destination_lat, destination_lon, api_key,
                         timeout=3, post=None):
    """取得多座停車場前往目的地的步行距離與時間。"""
    if not rows or not api_key:
        return {}
    request_post = post or requests.post
    try:
        destination_index = len(rows)
        locations = [
            [float(row["longitude"]), float(row["latitude"])] for row in rows
        ]
        locations.append([float(destination_lon), float(destination_lat)])
        payload = {
            "locations": locations,
            "sources": [str(index) for index in range(len(rows))],
            "destinations": [str(destination_index)],
            "metrics": ["distance", "duration"],
            "resolve_locations": False,
        }
        response = request_post(
            ORS_WALKING_MATRIX_URL,
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        distances = data["distances"]
        durations = data["durations"]
        result = {}
        for index, row in enumerate(rows):
            distance = distances[index][0]
            duration = durations[index][0]
            if distance is None or duration is None:
                continue
            result[row["lot_id"]] = {
                "walking_distance_m": round(float(distance), 1),
                "walking_duration_minutes": round(float(duration) / 60, 1),
            }
        return result
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        raise WalkingRouteError("步行路線服務暫時無法使用") from exc
