"""Pydantic response models (the public API contract)."""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import FileStatus, MeasurementStatus


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: str = Field(description="shapefile, kml or kmz")
    status: FileStatus
    feature_count: int
    crs: str | None = Field(description="Source CRS of the file (comma-separated if layers differ)")
    layers: list[str]
    warnings: list[str]
    error: str | None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        # SQLite drops timezone info; every timestamp we store is UTC.
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class FeatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(validation_alias="index", description="0-based index of the feature in the file")
    layer: str
    geometry_type: str | None
    crs: str | None
    properties: dict[str, Any]
    geometry: dict[str, Any] | None = Field(description="GeoJSON geometry in the source CRS")


class FeatureList(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    features: list[FeatureOut]


class FeatureMeasurement(BaseModel):
    feature_id: int
    layer: str
    name: str | None
    geometry_type: str | None
    status: MeasurementStatus
    area_m2: float | None = None
    area_km2: float | None = None
    perimeter_m: float | None = None
    length_m: float | None = None
    length_km: float | None = None
    projected_crs: str | None = Field(None, description="CRS the measurement was calculated in")
    note: str | None = None


class MeasurementSummary(BaseModel):
    feature_count: int
    measured_count: int
    total_area_m2: float
    total_length_m: float
    by_geometry_type: dict[str, int]
    by_status: dict[str, int]


class MeasurementsResponse(BaseModel):
    file_id: str
    filename: str
    crs: str | None
    summary: MeasurementSummary
    measurements: list[FeatureMeasurement]


class ErrorResponse(BaseModel):
    detail: str
