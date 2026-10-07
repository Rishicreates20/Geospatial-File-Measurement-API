"""Turn an uploaded file into one GeoDataFrame per layer.

Supported inputs:
  * .zip  containing one or more Shapefiles (.shp + .shx + .dbf, optional .prj)
  * .kml  (every KML Folder is read as its own layer)
  * .kmz  (zipped KML)

Reading is done by GDAL (via pyogrio). Zip archives are read in place through
GDAL's /vsizip/ virtual filesystem, so nothing is extracted to disk and
"zip slip" path traversal is not possible.
"""

import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import geopandas as gpd
import pyogrio

from app.services.errors import InvalidGeoFileError, UnsupportedFileTypeError

FILE_TYPES = {".zip": "shapefile", ".kml": "kml", ".kmz": "kmz"}
SHAPEFILE_REQUIRED_PARTS = (".shx", ".dbf")
# Styling/display fields GDAL's KML driver adds to every placemark; not real attributes.
KML_DISPLAY_FIELDS = ["tessellate", "extrude", "visibility", "drawOrder", "icon", "altitudeMode"]


@dataclass
class Layer:
    name: str
    gdf: gpd.GeoDataFrame


@dataclass
class ReadResult:
    layers: list[Layer]
    warnings: list[str] = field(default_factory=list)


def detect_file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in FILE_TYPES:
        allowed = ", ".join(sorted(FILE_TYPES))
        raise UnsupportedFileTypeError(f"Unsupported file type '{suffix or filename}'. Allowed: {allowed}.")
    return FILE_TYPES[suffix]


def read_layers(path: Path, file_type: str, max_uncompressed_bytes: int) -> ReadResult:
    warnings: list[str] = []
    if file_type == "shapefile":
        sources = _shapefile_sources(path, max_uncompressed_bytes, warnings)
    elif file_type == "kmz":
        sources = [_kmz_source(path, max_uncompressed_bytes)]
    else:
        sources = [str(path)]

    layers: list[Layer] = []
    for source in sources:
        layers.extend(_read_all_layers(source))

    if file_type in ("kml", "kmz"):
        for layer in layers:
            layer.gdf = layer.gdf.drop(columns=KML_DISPLAY_FIELDS, errors="ignore")
    return ReadResult(layers=layers, warnings=warnings)


def _read_all_layers(source: str) -> list[Layer]:
    try:
        layer_names = [name for name, _ in pyogrio.list_layers(source)]
    except Exception as exc:  # GDAL raises several exception types for unreadable data
        raise InvalidGeoFileError(f"Could not open geospatial data: {exc}") from exc

    layers = []
    for name in layer_names:
        try:
            gdf = gpd.read_file(source, layer=name, engine="pyogrio")
        except Exception as exc:
            raise InvalidGeoFileError(f"Could not read layer '{name}': {exc}") from exc
        if len(gdf):  # KML documents often contain an empty top-level layer
            layers.append(Layer(name=str(name), gdf=gdf))
    return layers


def _open_zip(path: Path, max_uncompressed_bytes: int) -> list[zipfile.ZipInfo]:
    if not zipfile.is_zipfile(path):
        raise InvalidGeoFileError("The uploaded file is not a valid zip archive.")
    with zipfile.ZipFile(path) as archive:
        members = [
            info
            for info in archive.infolist()
            if not info.is_dir()
            and not info.filename.startswith("__MACOSX/")
            and not PurePosixPath(info.filename).name.startswith(".")
        ]
    if sum(info.file_size for info in members) > max_uncompressed_bytes:
        raise InvalidGeoFileError("The archive is too large once uncompressed.")
    return members


def _shapefile_sources(path: Path, max_uncompressed_bytes: int, warnings: list[str]) -> list[str]:
    members = _open_zip(path, max_uncompressed_bytes)
    names = {m.filename.lower(): m.filename for m in members}
    shp_files = [m.filename for m in members if m.filename.lower().endswith(".shp")]
    if not shp_files:
        raise InvalidGeoFileError("The zip archive does not contain a .shp file.")

    sources = []
    for shp in shp_files:
        stem = shp[:-4]
        missing = [ext for ext in SHAPEFILE_REQUIRED_PARTS if (stem + ext).lower() not in names]
        if missing:
            raise InvalidGeoFileError(
                f"Shapefile '{shp}' is incomplete: missing {', '.join(missing)}."
            )
        if (stem + ".prj").lower() not in names:
            warnings.append(f"Shapefile '{shp}' has no .prj file, so its CRS is unknown.")
        sources.append(f"/vsizip/{path.resolve()}/{shp}")
    return sources


def _kmz_source(path: Path, max_uncompressed_bytes: int) -> str:
    members = _open_zip(path, max_uncompressed_bytes)
    kml_files = [m.filename for m in members if m.filename.lower().endswith(".kml")]
    if not kml_files:
        raise InvalidGeoFileError("The KMZ archive does not contain a .kml file.")
    # By convention the main document is doc.kml; otherwise take the first one.
    main = next((k for k in kml_files if PurePosixPath(k).name.lower() == "doc.kml"), kml_files[0])
    return f"/vsizip/{path.resolve()}/{main}"
