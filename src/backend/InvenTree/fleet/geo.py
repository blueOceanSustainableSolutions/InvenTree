"""Geographic helpers for the fleet app (pure python, no GIS dependencies)."""

import math
import re
import string
from decimal import Decimal, localcontext

# Mean radius of the earth (m)
EARTH_RADIUS_M = 6371008.8

# Coordinates are stored as signed decimal degrees, with this many decimal
# places (1e-10 degree is about 0.01 mm: any DD, DDM or DMS input converts
# back to the same digits)
COORDINATE_DECIMAL_PLACES = 10

# Largest absolute value of each axis
COORDINATE_LIMITS = {'latitude': 90, 'longitude': 180}

# Hemisphere letters of each axis, and their sign ('O' is Oeste, west)
COORDINATE_HEMISPHERES = {
    'latitude': {'N': 1, 'S': -1},
    'longitude': {'E': 1, 'W': -1, 'O': -1},
}

# Separators between degrees, minutes and seconds
_COORDINATE_SEPARATORS = re.compile(
    r'[\s:\'"\u00b0\u00ba\u02da\u2032\u2019\u2033\u201d]+'
)

_COORDINATE_NUMBER = re.compile(r'\d+([.,]\d+)?')


def to_float(value) -> float | None:
    """Convert a coordinate (Decimal, str, float or None) to a float."""
    if value is None or value == '':
        return None

    return float(value)


def parse_coordinate(value, axis: str) -> Decimal:
    """Parse a coordinate given in any of the common formats.

    Accepted formats (sign or hemisphere letter, comma or dot decimals):
        DD:  38.722256, -9.1393, 38.722256 N
        DDM: 38 43.3354, 38°43.3354'N
        DMS: 38 43 20.12, 38°43'20.12"N, W 9°8'21.5"

    Arguments:
        value: The coordinate (number or text)
        axis: 'latitude' or 'longitude'

    Returns:
        The coordinate in signed decimal degrees

    Raises:
        ValueError: The value is not a valid coordinate for the axis
    """
    limit = COORDINATE_LIMITS[axis]
    hemispheres = COORDINATE_HEMISPHERES[axis]
    letters = '/'.join(hemispheres)

    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        # Plain notation (str(1e-05) would be '1e-05')
        text = format(Decimal(str(value)), 'f')
    else:
        text = str(value).strip().upper()

    if not text:
        raise ValueError('Enter a coordinate')

    sign = 1

    # Hemisphere letter, at the start or at the end
    hemisphere = None

    if text[0] in string.ascii_uppercase:
        hemisphere, text = text[0], text[1:]
    elif text[-1] in string.ascii_uppercase:
        hemisphere, text = text[-1], text[:-1]

    if hemisphere is not None:
        if hemisphere not in hemispheres:
            raise ValueError(f'Hemisphere must be {letters}')
        sign = hemispheres[hemisphere]

    text = text.strip()

    if text and text[0] in '+-':
        if hemisphere is not None and text[0] == '-':
            raise ValueError('Use either a minus sign or a hemisphere letter')
        if text[0] == '-':
            sign = -sign
        text = text[1:]

    parts = [part for part in _COORDINATE_SEPARATORS.split(text) if part]

    if not 1 <= len(parts) <= 3 or not all(
        _COORDINATE_NUMBER.fullmatch(part) for part in parts
    ):
        raise ValueError(
            'Enter degrees, degrees and minutes, or degrees, minutes and seconds'
        )

    if any(not part.isdigit() for part in parts[:-1]):
        raise ValueError('Only the last number can have decimals')

    with localcontext() as ctx:
        ctx.prec = 40

        numbers = [Decimal(part.replace(',', '.')) for part in parts]

        for number in numbers[1:]:
            if number >= 60:
                raise ValueError('Minutes and seconds must be below 60')

        degrees = numbers[0]

        if len(numbers) > 1:
            degrees += numbers[1] / 60
        if len(numbers) > 2:
            degrees += numbers[2] / 3600

        if degrees > limit:
            raise ValueError(
                f'{axis.capitalize()} must be between -{limit} and {limit}'
            )

        result = (sign * degrees).quantize(
            Decimal(1).scaleb(-COORDINATE_DECIMAL_PLACES)
        )

    # Avoid a negative zero
    return result + 0


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
