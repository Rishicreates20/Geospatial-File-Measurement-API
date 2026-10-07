"""Per-feature measurement: area for polygons, length for lines, nothing for points."""

import logging
from dataclasses import dataclass

import shapely
from shapely.geometry.base import BaseGeometry

from app.models import MeasurementStatus
from app.services.crs import choose_projection, project

logger = logging.getLogger(__name__)

POLYGONAL = {"Polygon", "MultiPolygon"}
LINEAR = {"LineString", "MultiLineString", "LinearRing"}
PUNTAL = {"Point", "MultiPoint"}


@dataclass
class Measurement:
    status: MeasurementStatus
    area_m2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    projected_crs: str | None = None
    note: str | None = None


def measure_geometry(geom: BaseGeometry | None) -> Measurement:
    """Measure one geometry given in WGS84 lon/lat.

    Never raises: problems are reported in the result instead.
    """
    if geom is None or geom.is_empty:
        return Measurement(MeasurementStatus.UNSUPPORTED, note="Feature has no geometry.")

    geom_type = geom.geom_type
    if geom_type in PUNTAL:
        return Measurement(MeasurementStatus.NOT_APPLICABLE, note="Points have no area or length.")
    if geom_type not in POLYGONAL | LINEAR:
        return Measurement(
            MeasurementStatus.UNSUPPORTED,
            note=f"Measurement is not supported for {geom_type}.",
        )

    try:
        return _measure(geom, geom_type)
    except Exception as exc:  # one bad feature must not fail the whole file
        logger.warning("Measurement failed for %s: %s", geom_type, exc)
        return Measurement(MeasurementStatus.ERROR, note=f"Measurement failed: {exc}")


def _measure(geom: BaseGeometry, geom_type: str) -> Measurement:
    note = None
    geom = shapely.force_2d(geom)  # KML often carries altitude; measurements are planar

    if geom_type in POLYGONAL and not geom.is_valid:
        # e.g. a self-intersecting "bow tie": its raw area would cancel itself out.
        reason = shapely.is_valid_reason(geom)
        geom = shapely.make_valid(geom)
        note = f"Invalid polygon was repaired before measuring ({reason})."

    projection = choose_projection(geom, for_area=geom_type in POLYGONAL)
    projected = project(geom, projection)

    result = Measurement(MeasurementStatus.MEASURED, projected_crs=projection.name, note=note)
    if geom_type in POLYGONAL:
        # make_valid can return a GeometryCollection; only its polygon parts have area.
        result.area_m2 = projected.area
        result.perimeter_m = _polygon_perimeter(projected)
    else:
        result.length_m = projected.length
    return result


def _polygon_perimeter(geom: BaseGeometry) -> float:
    if geom.geom_type in POLYGONAL:
        return geom.length
    return sum(part.length for part in shapely.get_parts(geom) if part.geom_type in POLYGONAL)
