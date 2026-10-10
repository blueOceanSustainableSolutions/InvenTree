"""Geographic helpers for the fleet app (pure python, no GIS dependencies)."""

import math

# Mean radius of the earth (m)
EARTH_RADIUS_M = 6371008.8


def to_float(value) -> float | None:
    """Convert a coordinate (Decimal, str, float or None) to a float."""
    if value is None or value == '':
        return None

    return float(value)


def haversine(lat1, lon1, lat2, lon2) -> float:
    """Return the great-circle distance between two points, in metres."""
    lat1, lon1, lat2, lon2 = (to_float(v) for v in (lat1, lon1, lat2, lon2))

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )

    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def point_in_polygon(lat, lon, polygon) -> bool:
    """Return True if a point is inside a polygon (ray casting).

    Arguments:
        lat: Latitude of the point
        lon: Longitude of the point
        polygon: List of [latitude, longitude] points (open or closed ring)

    Points exactly on an edge count as inside. Polygons which cross the
    antimeridian are not supported.
    """
    x = to_float(lon)
    y = to_float(lat)

    points = [(to_float(p[1]), to_float(p[0])) for p in polygon or []]

    # Drop the closing point of a closed ring
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]

    if len(points) < 3:
        return False

    inside = False
    j = len(points) - 1

    for i in range(len(points)):
        xi, yi = points[i]
        xj, yj = points[j]

        # On a vertex or an edge
        if (xi, yi) == (x, y) or _on_segment(x, y, xi, yi, xj, yj):
            return True

        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi

            if x < x_cross:
                inside = not inside

        j = i

    return inside


def _on_segment(x, y, x1, y1, x2, y2, tolerance=1e-12) -> bool:
    """Return True if (x, y) lies on the segment (x1, y1) - (x2, y2)."""
    cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)

    if abs(cross) > tolerance:
        return False

    return min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2)


def check_geofence(
    lat, lon, nominal_lat=None, nominal_lon=None, radius_m=None, polygon=None
) -> tuple[bool | None, float | None]:
    """Check a position against a geofence.

    The polygon is used when it is set, otherwise the circle of radius_m
    around the nominal position.

    Returns:
        (inside, distance): inside is None when the geofence cannot be
        evaluated; distance is the distance from the nominal position (m), or
        None if there is no nominal position
    """
    if lat is None or lon is None:
        return None, None

    distance = None

    if nominal_lat is not None and nominal_lon is not None:
        distance = haversine(nominal_lat, nominal_lon, lat, lon)

    if polygon:
        return point_in_polygon(lat, lon, polygon), distance

    if distance is None or not radius_m:
        return None, distance

    return distance <= float(radius_m), distance
