"""CRS helpers: resolving a layer's source CRS and choosing a projected CRS per feature.

Strategy for measurement (see README "CRS handling"):
  1. Reproject each layer from its source CRS to WGS84 (EPSG:4326), once per layer.
  2. Build a projection centred on that feature (the centre of its bounding box):
       * polygons -> Lambert Azimuthal Equal-Area (LAEA). Equal-area projections
         preserve area exactly, at any size and anywhere on Earth.
       * lines    -> Transverse Mercator with scale factor 1 on the feature's own
         central meridian. Distortion grows with distance from the centre, so for
         features up to a few hundred km it is far below 0.01%.
  3. Measure in that projection's metres.

Why not UTM? UTM is conformal, not equal-area, and its scale error reaches about
0.1% in length (0.2% in area) near zone edges, plus it needs special cases at zone
boundaries and the poles. A projection centred on the feature avoids all of that.

Going through WGS84 first means a file that is already "projected" in a CRS that is
bad for measurement (e.g. Web Mercator, EPSG:3857) is still measured correctly.
"""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

WGS84 = CRS.from_epsg(4326)


def resolve_source_crs(crs: CRS | None, bounds: tuple[float, float, float, float]) -> tuple[CRS, str | None]:
    """Return the CRS to use for a layer, plus a warning if we had to assume one.

    Files without CRS information (e.g. a shapefile with no .prj) are assumed to be
    WGS84 when every coordinate is a plausible longitude/latitude. Otherwise we refuse
    to guess, because measuring in the wrong CRS gives silently wrong numbers.
    """
    if crs is not None:
        return CRS.from_user_input(crs), None

    minx, miny, maxx, maxy = bounds
    looks_geographic = -180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90
    if looks_geographic:
        return WGS84, "No CRS defined; coordinates look like longitude/latitude, so EPSG:4326 was assumed."
    raise ValueError("No CRS defined and coordinates are not longitude/latitude; cannot measure safely.")


def crs_label(crs: CRS) -> str:
    """Short human-readable identifier, e.g. 'EPSG:4326'."""
    authority = crs.to_authority()
    return f"{authority[0]}:{authority[1]}" if authority else crs.name


@dataclass(frozen=True)
class LocalProjection:
    """A metric projection centred on one feature, defined as a PROJ string."""

    name: str
    proj_string: str

    @property
    def crs(self) -> CRS:
        return CRS.from_proj4(self.proj_string)


def choose_projection(geom_wgs84: BaseGeometry, for_area: bool) -> LocalProjection:
    minx, miny, maxx, maxy = geom_wgs84.bounds
    lon, lat = round((minx + maxx) / 2, 6), round((miny + maxy) / 2, 6)

    if for_area:
        return LocalProjection(
            name=f"WGS 84 / LAEA centred on {lat}, {lon}",
            proj_string=f"+proj=laea +lat_0={lat} +lon_0={lon} +ellps=WGS84 +units=m",
        )
    return LocalProjection(
        name=f"WGS 84 / Transverse Mercator centred on {lat}, {lon}",
        proj_string=f"+proj=tmerc +lat_0={lat} +lon_0={lon} +k_0=1 +ellps=WGS84 +units=m",
    )


def project(geom_wgs84: BaseGeometry, projection: LocalProjection) -> BaseGeometry:
    """Project a lon/lat geometry into the local projection (coordinates in metres)."""
    transformer = _transformer(projection.proj_string)

    def _transform(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geom_wgs84, _transform)


@lru_cache(maxsize=1024)
def _transformer(proj_string: str) -> Transformer:
    # A direct pipeline (degrees -> radians -> projection) skips PROJ's operation search,
    # which matters because every feature gets its own projection.
    return Transformer.from_pipeline(
        f"+proj=pipeline +step +proj=unitconvert +xy_in=deg +xy_out=rad +step {proj_string}"
    )
