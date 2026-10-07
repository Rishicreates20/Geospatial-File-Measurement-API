"""End-to-end tests through the HTTP API."""

import io
import zipfile

import geopandas as gpd
import pytest
from pyproj import Geod
from shapely.geometry import LineString, Point, Polygon

from tests.conftest import kml_document, kml_line, kml_polygon, placemark, shapefile_zip, upload

GEOD = Geod(ellps="WGS84")  # ellipsoidal reference values to check our projected results against

SQUARE = [(77.50, 13.10), (77.51, 13.10), (77.51, 13.11), (77.50, 13.11), (77.50, 13.10)]
ROUTE = [(77.60, 12.97), (77.61, 12.98), (77.63, 12.98)]


def geodesic_area(coords):
    lons, lats = zip(*coords)
    return abs(GEOD.polygon_area_perimeter(lons, lats)[0])


def geodesic_length(coords):
    lons, lats = zip(*coords)
    return GEOD.line_length(lons, lats)


def measurements_by_name(client, file_id):
    body = client.get(f"/api/files/{file_id}/measurements/").json()
    return body, {m["name"]: m for m in body["measurements"]}


# --- KML -----------------------------------------------------------------------------


def test_kml_upload_returns_file_info(client):
    kml = kml_document(placemark("Plot", kml_polygon(SQUARE)), placemark("Well", "<Point><coordinates>77.5,13.1</coordinates></Point>"))
    response = upload(client, "survey.kml", kml)

    assert response.status_code == 201
    info = response.json()
    assert info["filename"] == "survey.kml"
    assert info["file_type"] == "kml"
    assert info["status"] == "COMPLETED"
    assert info["feature_count"] == 2
    assert info["crs"] == "EPSG:4326"
    assert client.get(f"/api/files/{info['id']}/").json() == info


def test_kml_measurements_match_geodesic_reference(client):
    kml = kml_document(
        placemark("Plot", kml_polygon(SQUARE)),
        placemark("Route", kml_line(ROUTE)),
        placemark("Well", "<Point><coordinates>77.5,13.1</coordinates></Point>"),
    )
    file_id = upload(client, "survey.kml", kml).json()["id"]
    body, by_name = measurements_by_name(client, file_id)

    plot = by_name["Plot"]
    assert plot["status"] == "MEASURED"
    assert plot["projected_crs"].startswith("WGS 84 / LAEA centred on")
    assert plot["area_m2"] == pytest.approx(geodesic_area(SQUARE), rel=1e-4)
    assert plot["length_m"] is None

    route = by_name["Route"]
    assert route["projected_crs"].startswith("WGS 84 / Transverse Mercator centred on")
    assert route["length_m"] == pytest.approx(geodesic_length(ROUTE), rel=1e-4)
    assert route["area_m2"] is None

    well = by_name["Well"]
    assert well["status"] == "NOT_APPLICABLE"
    assert well["area_m2"] is None and well["length_m"] is None

    summary = body["summary"]
    assert summary["feature_count"] == 3
    assert summary["measured_count"] == 2
    assert summary["total_area_m2"] == pytest.approx(plot["area_m2"], abs=0.01)
    assert summary["by_status"] == {"MEASURED": 2, "NOT_APPLICABLE": 1}


def test_unsupported_geometry_is_reported_not_crashed(client):
    multi = "<MultiGeometry><Point><coordinates>77.5,13.1</coordinates></Point>" + kml_line(ROUTE) + "</MultiGeometry>"
    file_id = upload(client, "mixed.kml", kml_document(placemark("Mixed", multi))).json()["id"]
    _, by_name = measurements_by_name(client, file_id)

    assert by_name["Mixed"]["geometry_type"] == "GeometryCollection"
    assert by_name["Mixed"]["status"] == "UNSUPPORTED"
    assert "GeometryCollection" in by_name["Mixed"]["note"]


def test_self_intersecting_polygon_is_repaired(client):
    bow_tie = [(77.50, 13.10), (77.51, 13.11), (77.51, 13.10), (77.50, 13.11), (77.50, 13.10)]
    file_id = upload(client, "bowtie.kml", kml_document(placemark("Bow tie", kml_polygon(bow_tie)))).json()["id"]
    _, by_name = measurements_by_name(client, file_id)

    result = by_name["Bow tie"]
    assert result["status"] == "MEASURED"
    assert result["area_m2"] > 0  # an unrepaired bow tie would report ~0
    assert "repaired" in result["note"]


def test_kml_folders_become_layers(client):
    kml = kml_document(
        "<Folder><name>Parks</name>" + placemark("Park", kml_polygon(SQUARE)) + "</Folder>",
        "<Folder><name>Roads</name>" + placemark("Road", kml_line(ROUTE)) + "</Folder>",
    )
    info = upload(client, "city.kml", kml).json()
    assert info["layers"] == ["Parks", "Roads"]
    assert info["feature_count"] == 2


def test_kmz_upload(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("doc.kml", kml_document(placemark("Plot", kml_polygon(SQUARE))))
    response = upload(client, "plot.kmz", buffer.getvalue())

    assert response.status_code == 201
    assert response.json()["file_type"] == "kmz"


# --- Shapefile -----------------------------------------------------------------------


def test_shapefile_zip_with_attributes(client):
    gdf = gpd.GeoDataFrame({"plot_id": ["A1"], "acres": [12.5]}, geometry=[Polygon(SQUARE)], crs="EPSG:4326")
    response = upload(client, "plots.zip", shapefile_zip(gdf, "plots"))

    assert response.status_code == 201
    info = response.json()
    assert info["file_type"] == "shapefile"
    assert info["layers"] == ["plots"]

    feature = client.get(f"/api/files/{info['id']}/features/").json()["features"][0]
    assert feature["id"] == 0
    assert feature["geometry_type"] == "Polygon"
    assert feature["crs"] == "EPSG:4326"
    assert feature["properties"] == {"plot_id": "A1", "acres": 12.5}
    assert feature["geometry"]["type"] == "Polygon"


def test_web_mercator_input_is_measured_correctly(client):
    """Web Mercator inflates areas by ~1/cos(lat)^2; we must not measure in it directly."""
    wgs84 = gpd.GeoDataFrame({"name": ["Plot"]}, geometry=[Polygon(SQUARE)], crs="EPSG:4326")
    mercator = wgs84.to_crs("EPSG:3857")
    file_id = upload(client, "plot.zip", shapefile_zip(mercator)).json()["id"]
    body, by_name = measurements_by_name(client, file_id)

    assert body["crs"] == "EPSG:3857"
    assert by_name["Plot"]["area_m2"] == pytest.approx(geodesic_area(SQUARE), rel=1e-4)
    assert by_name["Plot"]["area_m2"] < mercator.area.iloc[0] * 0.97  # naive Mercator area is too big


def test_projected_utm_input_reports_ground_distance(client):
    """UTM grid distances differ from true ground distance by the scale factor (~0.9996)."""
    line = LineString([(650000, 1440000), (651000, 1440000), (651000, 1441500)])
    gdf = gpd.GeoDataFrame({"name": ["Pipeline"]}, geometry=[line], crs="EPSG:32643")
    file_id = upload(client, "survey.zip", shapefile_zip(gdf)).json()["id"]
    _, by_name = measurements_by_name(client, file_id)

    dense = gpd.GeoSeries([line.segmentize(10)], crs="EPSG:32643").to_crs("EPSG:4326").iloc[0]
    assert by_name["Pipeline"]["length_m"] == pytest.approx(geodesic_length(dense.coords), rel=1e-5)
    assert by_name["Pipeline"]["length_m"] == pytest.approx(2500, rel=1e-3)  # grid length, roughly


def test_shapefile_without_prj_assumes_wgs84_with_warning(client):
    gdf = gpd.GeoDataFrame({"name": ["Plot"]}, geometry=[Polygon(SQUARE)], crs="EPSG:4326")
    info = upload(client, "plots.zip", shapefile_zip(gdf, drop=(".prj",))).json()

    assert info["status"] == "COMPLETED"
    assert info["crs"] == "EPSG:4326"
    assert any("no .prj" in w for w in info["warnings"])
    assert any("EPSG:4326 was assumed" in w for w in info["warnings"])


def test_shapefile_without_prj_and_projected_coords_fails(client):
    gdf = gpd.GeoDataFrame({"name": ["P"]}, geometry=[Point(650000, 1440000)], crs="EPSG:32643")
    response = upload(client, "points.zip", shapefile_zip(gdf, drop=(".prj",)))

    assert response.status_code == 422
    assert response.json()["status"] == "FAILED"
    assert "No CRS" in response.json()["error"]


# --- Validation and errors -----------------------------------------------------------


def test_zip_without_shapefile_fails_and_measurements_conflict(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hello")
    response = upload(client, "data.zip", buffer.getvalue())

    assert response.status_code == 422
    info = response.json()
    assert info["status"] == "FAILED"
    assert "does not contain a .shp" in info["error"]
    assert client.get(f"/api/files/{info['id']}/").json()["status"] == "FAILED"
    assert client.get(f"/api/files/{info['id']}/measurements/").status_code == 409


def test_incomplete_shapefile_fails(client):
    gdf = gpd.GeoDataFrame({"name": ["Plot"]}, geometry=[Polygon(SQUARE)], crs="EPSG:4326")
    response = upload(client, "plots.zip", shapefile_zip(gdf, drop=(".dbf",)))
    assert response.status_code == 422
    assert "missing .dbf" in response.json()["error"]


def test_corrupt_kml_fails_gracefully(client):
    response = upload(client, "broken.kml", b"<kml><Document><Placemark>")
    assert response.status_code == 422
    assert response.json()["status"] == "FAILED"


def test_not_a_zip_fails_gracefully(client):
    response = upload(client, "fake.zip", b"definitely not a zip")
    assert response.status_code == 422
    assert "not a valid zip" in response.json()["error"]


def test_unsupported_extension_is_rejected(client):
    response = upload(client, "data.csv", b"a,b\n1,2")
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]
    assert client.get("/api/files/").json() == []  # nothing stored


def test_upload_size_limit(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    small_client = TestClient(create_app(Settings(data_dir=tmp_path, max_upload_mb=0)))
    response = upload(small_client, "big.kml", kml_document(placemark("Plot", kml_polygon(SQUARE))))
    assert response.status_code == 413


def test_unknown_file_returns_404(client):
    assert client.get("/api/files/does-not-exist/").status_code == 404
    assert client.get("/api/files/does-not-exist/measurements/").status_code == 404


# --- Listing, pagination, deletion ---------------------------------------------------


def test_feature_pagination(client):
    placemarks = [placemark(f"P{i}", f"<Point><coordinates>77.5,13.{i}</coordinates></Point>") for i in range(5)]
    file_id = upload(client, "points.kml", kml_document(*placemarks)).json()["id"]

    page = client.get(f"/api/files/{file_id}/features/?limit=2&offset=2").json()
    assert page["total"] == 5
    assert [f["id"] for f in page["features"]] == [2, 3]
    assert page["features"][0]["properties"] == {"Name": "P2"}  # KML styling fields are dropped


def test_list_and_delete(client):
    file_id = upload(client, "plot.kml", kml_document(placemark("Plot", kml_polygon(SQUARE)))).json()["id"]
    assert [f["id"] for f in client.get("/api/files/").json()] == [file_id]

    assert client.delete(f"/api/files/{file_id}/").status_code == 204
    assert client.get(f"/api/files/{file_id}/").status_code == 404


def test_root_redirects_to_docs(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/docs"
