"""HTTP endpoints under /api/files/. Thin layer: validation and mapping only, logic lives in services."""

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Feature, FileStatus, MeasurementStatus, UploadedFile
from app.schemas import (
    ErrorResponse,
    FeatureList,
    FeatureMeasurement,
    FeatureOut,
    FileInfo,
    MeasurementsResponse,
    MeasurementSummary,
)
from app.services.errors import FileTooLargeError, UnsupportedFileTypeError
from app.services.processor import delete_upload, process_upload

router = APIRouter(prefix="/api/files", tags=["files"])

NAME_KEYS = ("name", "Name", "NAME")


@router.post(
    "/",
    response_model=FileInfo,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Unsupported file type"},
        413: {"model": ErrorResponse, "description": "File too large"},
        422: {"model": FileInfo, "description": "File stored but could not be processed (status FAILED)"},
    },
)
def upload_file(file: UploadFile, request: Request, db: Session = Depends(get_db)):
    """Upload a .zip (Shapefile), .kml or .kmz file. It is processed and measured synchronously."""
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A filename is required.")
    try:
        record = process_upload(db, request.app.state.settings, file.filename, file.file)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc

    info = FileInfo.model_validate(record)
    if record.status == FileStatus.FAILED:
        return JSONResponse(status_code=422, content=info.model_dump(mode="json"))
    return info


@router.get("/", response_model=list[FileInfo])
def list_files(
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    query = select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(limit).offset(offset)
    return db.scalars(query).all()


@router.get("/{file_id}/", response_model=FileInfo, responses={404: {"model": ErrorResponse}})
def get_file(file_id: str, db: Session = Depends(get_db)):
    return _get_file_or_404(db, file_id)


@router.get("/{file_id}/features/", response_model=FeatureList, responses={404: {"model": ErrorResponse}})
def list_features(
    file_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Every feature with its index, geometry type, GeoJSON geometry, CRS and properties."""
    _get_file_or_404(db, file_id)
    total = db.scalar(select(func.count()).select_from(Feature).where(Feature.file_id == file_id))
    query = (
        select(Feature).where(Feature.file_id == file_id).order_by(Feature.index).limit(limit).offset(offset)
    )
    features = [FeatureOut.model_validate(f) for f in db.scalars(query)]
    return FeatureList(file_id=file_id, total=total or 0, limit=limit, offset=offset, features=features)


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def get_measurements(file_id: str, db: Session = Depends(get_db)):
    """Area (polygons) and length (lines) per feature, plus totals across the file."""
    record = _get_file_or_404(db, file_id)
    if record.status != FileStatus.COMPLETED:
        detail = f"File is {record.status.value}" + (f": {record.error}" if record.error else ".")
        raise HTTPException(status.HTTP_409_CONFLICT, detail)

    measurements = [_to_measurement(f) for f in record.features]
    summary = MeasurementSummary(
        feature_count=len(measurements),
        measured_count=sum(m.status == MeasurementStatus.MEASURED for m in measurements),
        total_area_m2=round(sum(m.area_m2 or 0 for m in measurements), 3),
        total_length_m=round(sum(m.length_m or 0 for m in measurements), 3),
        by_geometry_type=dict(Counter(m.geometry_type or "None" for m in measurements)),
        by_status=dict(Counter(m.status.value for m in measurements)),
    )
    return MeasurementsResponse(
        file_id=record.id, filename=record.filename, crs=record.crs, summary=summary, measurements=measurements
    )


@router.delete("/{file_id}/", status_code=status.HTTP_204_NO_CONTENT, responses={404: {"model": ErrorResponse}})
def delete_file(file_id: str, db: Session = Depends(get_db)):
    delete_upload(db, _get_file_or_404(db, file_id))


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"File '{file_id}' not found.")
    return record


def _to_measurement(feature: Feature) -> FeatureMeasurement:
    name = next((str(feature.properties[k]) for k in NAME_KEYS if k in feature.properties), None)
    return FeatureMeasurement(
        feature_id=feature.index,
        layer=feature.layer,
        name=name,
        geometry_type=feature.geometry_type,
        status=feature.measurement_status,
        area_m2=_round(feature.area_m2, 3),
        area_km2=_round(feature.area_m2 and feature.area_m2 / 1e6, 6),
        perimeter_m=_round(feature.perimeter_m, 3),
        length_m=_round(feature.length_m, 3),
        length_km=_round(feature.length_m and feature.length_m / 1e3, 6),
        projected_crs=feature.projected_crs,
        note=feature.measurement_note,
    )


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)
