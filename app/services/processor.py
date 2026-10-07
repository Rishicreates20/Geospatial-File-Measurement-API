"""Upload pipeline: save file -> read layers -> resolve CRS -> measure features -> persist."""

import json
import logging
import math
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
import pandas as pd
import shapely
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Feature, FileStatus, UploadedFile
from app.services.crs import WGS84, crs_label, resolve_source_crs
from app.services.errors import FileTooLargeError, InvalidGeoFileError
from app.services.measurement import measure_geometry
from app.services.readers import Layer, detect_file_type, read_layers

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024


def process_upload(db: Session, settings: Settings, filename: str, stream: BinaryIO) -> UploadedFile:
    """Store and process one upload. Returns the file record (COMPLETED or FAILED).

    Raises UnsupportedFileTypeError / FileTooLargeError before anything is stored.
    """
    file_type = detect_file_type(filename)
    record = UploadedFile(filename=Path(filename).name, file_type=file_type, stored_path="")
    db.add(record)
    db.flush()  # assigns record.id

    target_dir = settings.upload_dir / record.id
    stored_path = target_dir / f"upload{Path(filename).suffix.lower()}"
    try:
        _save_stream(stream, stored_path, settings.max_upload_mb * 1024 * 1024)
    except FileTooLargeError:
        db.rollback()
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    record.stored_path = str(stored_path)
    db.commit()  # the file now exists with status PROCESSING

    try:
        _extract_and_measure(record, stored_path, settings)
        record.status = FileStatus.COMPLETED
    except InvalidGeoFileError as exc:
        _mark_failed(record, str(exc))
    except Exception:
        logger.exception("Unexpected error while processing file %s", record.id)
        _mark_failed(record, "Unexpected error while processing the file.")
    db.commit()
    return record


def delete_upload(db: Session, record: UploadedFile) -> None:
    if record.stored_path:
        shutil.rmtree(Path(record.stored_path).parent, ignore_errors=True)
    db.delete(record)
    db.commit()


def _save_stream(stream: BinaryIO, path: Path, max_bytes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("wb") as out:
        while chunk := stream.read(CHUNK_SIZE):
            written += len(chunk)
            if written > max_bytes:
                raise FileTooLargeError(f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit.")
            out.write(chunk)


def _mark_failed(record: UploadedFile, message: str) -> None:
    record.status = FileStatus.FAILED
    record.error = message
    record.features = []
    record.feature_count = 0


def _extract_and_measure(record: UploadedFile, path: Path, settings: Settings) -> None:
    result = read_layers(path, record.file_type, settings.max_uncompressed_mb * 1024 * 1024)
    if not result.layers:
        raise InvalidGeoFileError("The file contains no features.")

    warnings = list(result.warnings)
    crs_labels: list[str] = []
    features: list[Feature] = []

    for layer in result.layers:
        source_crs = _layer_crs(layer, warnings)
        label = crs_label(source_crs)
        if label not in crs_labels:
            crs_labels.append(label)

        # Reproject the whole layer to lon/lat once (vectorised); measurement then
        # projects each feature into its own local metric CRS.
        geoms_wgs84 = layer.gdf.geometry.set_crs(source_crs, allow_override=True).to_crs(WGS84)
        property_columns = [c for c in layer.gdf.columns if c != layer.gdf.geometry.name]
        for row_props, geom, geom_wgs84 in zip(
            layer.gdf[property_columns].to_dict("records"), layer.gdf.geometry, geoms_wgs84, strict=True
        ):
            measurement = measure_geometry(geom_wgs84)
            features.append(
                Feature(
                    index=len(features),
                    layer=layer.name,
                    geometry_type=geom.geom_type if geom is not None else None,
                    crs=label,
                    geometry=json.loads(shapely.to_geojson(geom)) if geom is not None else None,
                    properties=_clean_properties(row_props),
                    measurement_status=measurement.status,
                    area_m2=measurement.area_m2,
                    perimeter_m=measurement.perimeter_m,
                    length_m=measurement.length_m,
                    projected_crs=measurement.projected_crs,
                    measurement_note=measurement.note,
                )
            )

    record.features = features
    record.feature_count = len(features)
    record.layers = [layer.name for layer in result.layers]
    record.crs = ", ".join(crs_labels)
    record.warnings = warnings


def _layer_crs(layer: Layer, warnings: list[str]):
    try:
        crs, warning = resolve_source_crs(layer.gdf.crs, tuple(layer.gdf.total_bounds))
    except ValueError as exc:
        raise InvalidGeoFileError(f"Layer '{layer.name}': {exc}") from exc
    if warning:
        warnings.append(f"Layer '{layer.name}': {warning}")
    return crs


def _clean_properties(props: dict[str, Any]) -> dict[str, Any]:
    """Make attribute values JSON-safe and drop empty ones (KML adds many blank fields)."""
    cleaned = {}
    for key, value in props.items():
        value = _to_json_value(value)
        if value is not None and value != "":
            cleaned[str(key)] = value
    return cleaned


def _to_json_value(value: Any) -> Any:
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return None
    return str(value)
