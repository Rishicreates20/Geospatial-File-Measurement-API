import io
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """An app with its own temporary database and upload folder."""
    return TestClient(create_app(Settings(data_dir=tmp_path)))


def upload(client: TestClient, filename: str, content: bytes):
    return client.post("/api/files/", files={"file": (filename, io.BytesIO(content))})


def shapefile_zip(gdf: gpd.GeoDataFrame, name: str = "layer", drop: tuple[str, ...] = ()) -> bytes:
    """Write a GeoDataFrame as a zipped shapefile, optionally leaving out parts like '.prj'."""
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        gdf.to_file(Path(tmp) / f"{name}.shp", engine="pyogrio")
        with zipfile.ZipFile(buffer, "w") as archive:
            for part in Path(tmp).iterdir():
                if part.suffix not in drop:
                    archive.write(part, part.name)
    return buffer.getvalue()


def kml_document(*placemarks: str) -> bytes:
    body = "\n".join(placemarks)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"{body}</Document></kml>"
    ).encode()


def placemark(name: str, geometry_xml: str) -> str:
    return f"<Placemark><name>{name}</name>{geometry_xml}</Placemark>"


def kml_polygon(coords: list[tuple[float, float]]) -> str:
    text = " ".join(f"{x},{y}" for x, y in coords)
    return f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{text}</coordinates></LinearRing></outerBoundaryIs></Polygon>"


def kml_line(coords: list[tuple[float, float]]) -> str:
    text = " ".join(f"{x},{y}" for x, y in coords)
    return f"<LineString><coordinates>{text}</coordinates></LineString>"
