"""Unit tests for CRS selection and the measurement service (no HTTP)."""

import pytest
import shapely
from pyproj import CRS, Geod
from shapely.geometry import LineString, MultiPolygon, Point, Polygon

from app.models import MeasurementStatus
from app.services.crs import WGS84, choose_projection, project, resolve_source_crs
from app.services.measurement import measure_geometry

GEOD = Geod(ellps="WGS84")


def test_projection_is_centred_on_the_feature():
    plot = Polygon([(77.50, 13.10), (77.52, 13.10), (77.52, 13.12), (77.50, 13.12)])
    area_projection = choose_projection(plot, for_area=True)
    length_projection = choose_projection(plot, for_area=False)

    assert "+proj=laea +lat_0=13.11 +lon_0=77.51" in area_projection.proj_string
    assert "+proj=tmerc +lat_0=13.11 +lon_0=77.51" in length_projection.proj_string
    assert area_projection.crs.is_projected

    # The feature's centre maps to the projection origin.
    origin = project(Point(77.51, 13.11), area_projection)
    assert origin.x == pytest.approx(0, abs=1e-6) and origin.y == pytest.approx(0, abs=1e-6)


@pytest.mark.parametrize(
    "coords",
    [
        [(77.50, 13.10), (77.51, 13.10), (77.51, 13.11), (77.50, 13.11)],  # 1 km square, Bengaluru
        [(-0.2, 51.4), (0.0, 51.4), (0.0, 51.6), (-0.2, 51.6)],  # London, higher latitude
        [(10, 84.5), (40, 84.5), (40, 85.5), (10, 85.5)],  # Arctic, where UTM is undefined
        [(70, 10), (80, 10), (80, 20), (70, 20)],  # ~1,100 km wide
    ],
)
def test_polygon_area_matches_geodesic_reference(coords):
    # Densify edges so both methods agree on the shape between vertices.
    polygon = shapely.segmentize(Polygon(coords), max_segment_length=0.01)
    lons, lats = polygon.exterior.xy
    expected = abs(GEOD.polygon_area_perimeter(lons, lats)[0])

    result = measure_geometry(polygon)
    assert result.status == MeasurementStatus.MEASURED
    assert result.area_m2 == pytest.approx(expected, rel=1e-5)


@pytest.mark.parametrize(
    "coords",
    [
        [(77.50, 13.10), (77.60, 13.15), (77.70, 13.10)],  # ~22 km road
        [(-74.0, 40.7), (-73.5, 41.0)],  # New York, ~53 km
    ],
)
def test_line_length_matches_geodesic_reference(coords):
    line = shapely.segmentize(LineString(coords), max_segment_length=0.01)
    lons, lats = line.xy
    expected = GEOD.line_length(lons, lats)

    assert measure_geometry(line).length_m == pytest.approx(expected, rel=1e-5)


def test_multipolygon_area_is_sum_of_parts():
    a = Polygon([(77.50, 13.10), (77.51, 13.10), (77.51, 13.11), (77.50, 13.11)])
    b = Polygon([(77.52, 13.10), (77.53, 13.10), (77.53, 13.11), (77.52, 13.11)])
    combined = measure_geometry(MultiPolygon([a, b])).area_m2
    separate = measure_geometry(a).area_m2 + measure_geometry(b).area_m2
    assert combined == pytest.approx(separate, rel=1e-6)


def test_3d_line_is_measured_in_2d():
    flat = measure_geometry(LineString([(77.5, 13.1), (77.6, 13.1)])).length_m
    with_altitude = measure_geometry(LineString([(77.5, 13.1, 0), (77.6, 13.1, 5000)])).length_m
    assert with_altitude == pytest.approx(flat)


def test_empty_and_missing_geometry():
    assert measure_geometry(None).status == MeasurementStatus.UNSUPPORTED
    assert measure_geometry(Polygon()).status == MeasurementStatus.UNSUPPORTED


def test_resolve_source_crs():
    assert resolve_source_crs(CRS.from_epsg(3857), (0, 0, 1, 1)) == (CRS.from_epsg(3857), None)

    crs, warning = resolve_source_crs(None, (77.5, 13.1, 77.6, 13.2))
    assert crs == WGS84 and "assumed" in warning

    with pytest.raises(ValueError):
        resolve_source_crs(None, (650000, 1440000, 651000, 1441000))
