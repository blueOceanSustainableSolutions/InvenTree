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
