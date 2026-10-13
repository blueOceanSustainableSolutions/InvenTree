"""Unit tests for the fleet geographic helpers (phase P2)."""

from decimal import Decimal

from django.test import SimpleTestCase

from fleet import geo

# Metres per degree of latitude
M_PER_DEG = 111195.0


class HaversineTest(SimpleTestCase):
    """Great-circle distances."""

    def test_zero(self):
        """The distance from a point to itself is zero."""
        self.assertEqual(geo.haversine(37.0, -7.9, 37.0, -7.9), 0)

    def test_known_distances(self):
        """Distances match known values."""
        # One degree of latitude is about 111.2 km
        self.assertAlmostEqual(geo.haversine(0, 0, 1, 0), M_PER_DEG, delta=1)

        # Lisbon to Faro is about 216 km
        distance = geo.haversine(38.7223, -9.1393, 37.0194, -7.9304)
        self.assertAlmostEqual(distance / 1000, 216.5, delta=2)

    def test_decimal_and_string(self):
        """Model values (Decimal) and strings are accepted."""
        distance = geo.haversine(Decimal('37.000000'), '-7.9', 37.0, Decimal('-7.9'))
        self.assertEqual(distance, 0)

    def test_short_distance(self):
        """A point 340 m north is 340 m away."""
        distance = geo.haversine(37.0, -7.9, 37.0 + 340 / M_PER_DEG, -7.9)
        self.assertAlmostEqual(distance, 340, delta=0.5)


class PolygonTest(SimpleTestCase):
    """Point in polygon."""

    SQUARE = [[37.0, -8.0], [37.0, -7.0], [38.0, -7.0], [38.0, -8.0]]

    def test_inside_outside(self):
        """Points inside and outside a square."""
        self.assertTrue(geo.point_in_polygon(37.5, -7.5, self.SQUARE))
        self.assertFalse(geo.point_in_polygon(36.5, -7.5, self.SQUARE))
        self.assertFalse(geo.point_in_polygon(37.5, -6.5, self.SQUARE))

    def test_closed_ring(self):
        """A closed ring (first point repeated) gives the same result."""
        closed = [*self.SQUARE, self.SQUARE[0]]

        self.assertTrue(geo.point_in_polygon(37.5, -7.5, closed))
        self.assertFalse(geo.point_in_polygon(39.0, -7.5, closed))

    def test_edges_and_vertices(self):
        """Points on an edge or a vertex are inside."""
        self.assertTrue(geo.point_in_polygon(37.0, -7.5, self.SQUARE))
        self.assertTrue(geo.point_in_polygon(38.0, -7.0, self.SQUARE))

    def test_concave(self):
        """A concave (L shaped) polygon."""
        shape = [[0, 0], [0, 2], [1, 2], [1, 1], [2, 1], [2, 0]]

        self.assertTrue(geo.point_in_polygon(0.5, 1.5, shape))
        self.assertTrue(geo.point_in_polygon(1.5, 0.5, shape))
        self.assertFalse(geo.point_in_polygon(1.5, 1.5, shape))

    def test_degenerate(self):
        """Fewer than three points is never inside."""
        self.assertFalse(geo.point_in_polygon(0, 0, []))
        self.assertFalse(geo.point_in_polygon(0, 0, [[0, 0], [1, 1]]))


class GeofenceTest(SimpleTestCase):
    """Geofence checks (radius or polygon)."""

    def test_radius(self):
        """Inside and outside a circular geofence."""
        north_150 = 37.0 + 150 / M_PER_DEG
        north_340 = 37.0 + 340 / M_PER_DEG

        inside, distance = geo.check_geofence(north_150, -7.9, 37.0, -7.9, 200)
        self.assertTrue(inside)
        self.assertAlmostEqual(distance, 150, delta=0.5)

        inside, distance = geo.check_geofence(north_340, -7.9, 37.0, -7.9, 200)
        self.assertFalse(inside)
        self.assertAlmostEqual(distance, 340, delta=0.5)

    def test_polygon_wins(self):
        """When a polygon is set, it is used instead of the radius."""
        polygon = [[36.9, -8.0], [36.9, -7.8], [37.1, -7.8], [37.1, -8.0]]
        north_340 = 37.0 + 340 / M_PER_DEG

        inside, distance = geo.check_geofence(north_340, -7.9, 37.0, -7.9, 200, polygon)
        self.assertTrue(inside)
        self.assertAlmostEqual(distance, 340, delta=0.5)

        inside, _distance = geo.check_geofence(37.2, -7.9, 37.0, -7.9, 200, polygon)
        self.assertFalse(inside)

    def test_cannot_evaluate(self):
        """No position, or no nominal position and no polygon."""
        self.assertEqual(geo.check_geofence(None, None, 37, -7.9, 200), (None, None))
        self.assertEqual(geo.check_geofence(37, -7.9, None, None, 200), (None, None))

        inside, distance = geo.check_geofence(37, -7.9, 37, -7.9, None)
        self.assertIsNone(inside)
        self.assertEqual(distance, 0)


class ParseCoordinateTest(SimpleTestCase):
    """Coordinates given as DD, DDM or DMS."""

    def test_decimal_degrees(self):
        """Signed numbers and hemisphere letters, dot or comma decimals."""
        self.assertEqual(
            geo.parse_coordinate('38.722256', 'latitude'), Decimal('38.722256')
        )
        self.assertEqual(
            geo.parse_coordinate('-9.1393', 'longitude'), Decimal('-9.1393')
        )
        self.assertEqual(geo.parse_coordinate('38,5 S', 'latitude'), Decimal('-38.5'))
        self.assertEqual(geo.parse_coordinate('N 38.5', 'latitude'), Decimal('38.5'))
        self.assertEqual(geo.parse_coordinate('9.5W', 'longitude'), Decimal('-9.5'))
        self.assertEqual(geo.parse_coordinate('9.5 O', 'longitude'), Decimal('-9.5'))
        self.assertEqual(geo.parse_coordinate('+12', 'longitude'), Decimal('12'))

    def test_degrees_minutes(self):
        """Degrees and decimal minutes."""
        self.assertEqual(
            geo.parse_coordinate('38 43.3354', 'latitude'), Decimal('38.7222566667')
        )
        self.assertEqual(
            geo.parse_coordinate("38°43,5' N", 'latitude'), Decimal('38.725')
        )
        self.assertEqual(geo.parse_coordinate("38º 30'", 'latitude'), Decimal('38.5'))

    def test_degrees_minutes_seconds(self):
        """Degrees, minutes and seconds, with ASCII or typographic marks."""
        expected = Decimal('38.7222555556')
        self.assertEqual(geo.parse_coordinate('38°43\'20.12"N', 'latitude'), expected)
        self.assertEqual(geo.parse_coordinate('38°43′20.12″N', 'latitude'), expected)  # noqa: RUF001
        self.assertEqual(geo.parse_coordinate('38 43 20.12', 'latitude'), expected)
        self.assertEqual(geo.parse_coordinate('38:43:20.12', 'latitude'), expected)
        self.assertEqual(
            geo.parse_coordinate('W 9 8 21.5', 'longitude'), Decimal('-9.1393055556')
        )

    def test_round_trip(self):
        """The stored precision gives back the seconds that were entered."""
        value = geo.parse_coordinate('38 43 20.1234', 'latitude')
        seconds = (value - 38) * 3600 - 43 * 60
        self.assertAlmostEqual(float(seconds), 20.1234, places=5)

    def test_invalid(self):
        """Invalid coordinates are rejected."""
        invalid = [
            ('', 'latitude'),
            ('abc', 'latitude'),
            ('91', 'latitude'),
            ('181', 'longitude'),
            ('90 0 1', 'latitude'),
            ('38 60', 'latitude'),
            ('38 43 60', 'latitude'),
            ('38.5 43', 'latitude'),
            ('38 43.5 10', 'latitude'),
            ('-38 S', 'latitude'),
            ('38 E', 'latitude'),
            ('9 N', 'longitude'),
            ('38 43 20 10', 'latitude'),
            ('38.7, -9.1', 'latitude'),
            ('38d43m20s', 'latitude'),
        ]

        for value, axis in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                geo.parse_coordinate(value, axis)
