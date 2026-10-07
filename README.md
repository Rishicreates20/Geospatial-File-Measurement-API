# Geospatial File Measurement API

A FastAPI service that accepts a geospatial file (a zipped **Shapefile**, **KML** or **KMZ**), extracts every feature, and returns accurate measurements: **area** for polygons and **length** for lines, calculated in a metric projection chosen for each feature.

```
POST /api/files/                    upload + process a file
GET  /api/files/{id}/               file information
GET  /api/files/{id}/measurements/  area / length per feature + totals
GET  /api/files/{id}/features/      geometry, CRS and properties per feature
```

---

## Setup

Requires Python 3.11+ (developed on 3.13). GDAL is **not** needed separately: it ships inside the `pyogrio` wheel.

```bash
git clone <your-repo-url> geo-measure-api
cd geo-measure-api

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs for interactive Swagger docs, where you can upload files from the browser.

Run the tests:

```bash
pytest
```

Or with Docker:

```bash
docker build -t geo-measure-api .
docker run -p 8000:8000 -v "$(pwd)/data:/data" geo-measure-api
```

### Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `GEO_DATA_DIR` | `./data` | Uploaded files and the SQLite database |
| `GEO_DATABASE_URL` | `sqlite:///<data dir>/app.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `GEO_MAX_UPLOAD_MB` | `50` | Maximum upload size |
| `GEO_MAX_UNCOMPRESSED_MB` | `500` | Zip-bomb guard for `.zip` / `.kmz` |

### Sample files

`samples/` contains ready-to-upload files:

| File | What it shows |
|---|---|
| `bengaluru.kml` | Polygons (one with a hole), a line, a point, and a mixed `MultiGeometry` that is reported as unsupported. Folders become layers. |
| `farm_plots_wgs84.zip` | Shapefile in EPSG:4326 (lat/lon degrees) that must be reprojected before measuring. |
| `survey_utm43n.zip` | Two shapefiles (lines + points) in one zip, already in a projected CRS (UTM 43N). |

Regenerate the shapefiles with `python scripts/make_sample_shapefiles.py`.

---

## API

### `POST /api/files/` — upload and process

Multipart form with a single field `file`. Accepted: `.zip` (one or more shapefiles), `.kml`, `.kmz`.

```bash
curl -F "file=@samples/survey_utm43n.zip" http://127.0.0.1:8000/api/files/
```

`201 Created`

```json
{
  "id": "4a2969e124e24bb0b0e1eb0fae3295f6",
  "filename": "survey_utm43n.zip",
  "file_type": "shapefile",
  "status": "COMPLETED",
  "feature_count": 3,
  "crs": "EPSG:32643",
  "layers": ["benchmarks", "survey_lines"],
  "warnings": [],
  "error": null,
  "created_at": "2026-10-07T15:05:32.440421Z"
}
```

| Status | When |
|---|---|
| `201` | Processed, `status: COMPLETED` |
| `400` | Unsupported extension (nothing is stored) |
| `413` | File larger than `GEO_MAX_UPLOAD_MB` |
| `422` | File stored but unreadable, `status: FAILED` with an `error` message (e.g. zip without a `.shp`, shapefile missing its `.dbf`, corrupt KML, no CRS and non-lat/lon coordinates) |

### `GET /api/files/{id}/` — file information

Returns the same object as the upload response. `GET /api/files/` lists all files (`?limit=&offset=`), and `DELETE /api/files/{id}/` removes a file and its features.

### `GET /api/files/{id}/measurements/` — measurements

```bash
curl http://127.0.0.1:8000/api/files/4a2969e124e24bb0b0e1eb0fae3295f6/measurements/
```

```json
{
  "file_id": "4a2969e124e24bb0b0e1eb0fae3295f6",
  "filename": "survey_utm43n.zip",
  "crs": "EPSG:32643",
  "summary": {
    "feature_count": 3,
    "measured_count": 2,
    "total_area_m2": 0.0,
    "total_length_m": 3000.354,
    "by_geometry_type": { "Point": 1, "LineString": 2 },
    "by_status": { "NOT_APPLICABLE": 1, "MEASURED": 2 }
  },
  "measurements": [
    {
      "feature_id": 0, "layer": "benchmarks", "name": "BM-1",
      "geometry_type": "Point", "status": "NOT_APPLICABLE",
      "area_m2": null, "area_km2": null, "perimeter_m": null, "length_m": null, "length_km": null,
      "projected_crs": null, "note": "Points have no area or length."
    },
    {
      "feature_id": 1, "layer": "survey_lines", "name": "Pipeline",
      "geometry_type": "LineString", "status": "MEASURED",
      "area_m2": null, "area_km2": null, "perimeter_m": null,
      "length_m": 2500.297, "length_km": 2.500297,
      "projected_crs": "WGS 84 / Transverse Mercator centred on 13.028942, 76.387885",
      "note": null
    }
  ]
}
```

A polygon result looks like this (from `bengaluru.kml`):

```json
{
  "feature_id": 1, "layer": "Parks", "name": "Cubbon Park (approx.)",
  "geometry_type": "Polygon", "status": "MEASURED",
  "area_m2": 1138771.107, "area_km2": 1.138771, "perimeter_m": 4111.563,
  "length_m": null, "length_km": null,
  "projected_crs": "WGS 84 / LAEA centred on 12.97575, 77.59425",
  "note": null
}
```

Measurement `status` values:

| Status | Meaning |
|---|---|
| `MEASURED` | Polygon / MultiPolygon → `area_m2` + `perimeter_m`; LineString / MultiLineString → `length_m` |
| `NOT_APPLICABLE` | Point / MultiPoint: nothing to measure |
| `UNSUPPORTED` | GeometryCollection, or a feature with no geometry |
| `ERROR` | Measuring this one feature failed; the rest of the file is unaffected |

Returns `409` if the file's status is `FAILED`.

### `GET /api/files/{id}/features/` — features

Every feature with its index, layer, geometry type, CRS, properties and GeoJSON geometry (in the source CRS). Paginated with `?limit=` (default 100) and `?offset=`.

```json
{
  "file_id": "4a2969e124e24bb0b0e1eb0fae3295f6",
  "total": 3, "limit": 1, "offset": 1,
  "features": [
    {
      "id": 1,
      "layer": "survey_lines",
      "geometry_type": "LineString",
      "crs": "EPSG:32643",
      "properties": { "name": "Pipeline" },
      "geometry": {
        "type": "LineString",
        "coordinates": [[650000.0, 1440000.0], [651000.0, 1440000.0], [651000.0, 1441500.0]]
      }
    }
  ]
}
```

---

## Architecture

### Application structure

```
app/
├── main.py              App factory: settings, DB session factory, routers
├── config.py            Settings from environment variables
├── database.py          SQLAlchemy engine/session + get_db dependency
├── models.py            ORM: UploadedFile 1──* Feature (feature row also holds its measurement)
├── schemas.py           Pydantic response models (the API contract)
├── api/
│   └── files.py         HTTP layer only: validation, status codes, response shaping
└── services/
    ├── processor.py     Orchestrates the upload pipeline and persistence
    ├── readers.py       .zip / .kml / .kmz → one GeoDataFrame per layer
    ├── crs.py           Source-CRS resolution + per-feature projection choice
    ├── measurement.py   Area / length for one geometry
    └── errors.py        Domain exceptions, mapped to HTTP codes by the API layer
tests/                   API tests (end to end) + unit tests for CRS and measurement
samples/                 Example input files
```

The HTTP layer knows nothing about GIS, and the services know nothing about HTTP. That keeps each piece small and testable on its own: `measurement.py` is unit-tested without a server, and the API tests exercise the whole flow through real HTTP requests against a temporary database.

### File-processing flow

```
POST /api/files/
  │
  ├─ 1. Check extension (.zip / .kml / .kmz)              → 400 if not allowed
  ├─ 2. Stream to disk in 1 MB chunks, enforce size limit → 413 if too big
  ├─ 3. Create UploadedFile row (status PROCESSING)
  ├─ 4. Read layers with GDAL (pyogrio)
  │      .zip: list members, require .shp + .shx + .dbf per shapefile, warn if no .prj,
  │            read in place via /vsizip/ (no extraction → no zip-slip)
  │      .kml: every <Folder> is a layer; GDAL's styling fields are dropped
  │      .kmz: find doc.kml inside the zip, read via /vsizip/
  ├─ 5. Per layer: resolve source CRS, reproject the layer to WGS84 once
  ├─ 6. Per feature: measure, store geometry (GeoJSON) + cleaned properties + result
  └─ 7. status COMPLETED (201)   or   FAILED with a readable error (422)
```

Measurements are computed once at upload and stored, so `GET .../measurements/` is a cheap database read.

### Measurement calculation flow

For each feature (`services/measurement.py`):

1. No geometry or empty → `UNSUPPORTED`. Point / MultiPoint → `NOT_APPLICABLE`. GeometryCollection → `UNSUPPORTED`.
2. Drop Z values (KML often carries altitude); measurements are on the ellipsoid surface.
3. Invalid polygons (for example a self-intersecting "bow tie", whose raw area cancels out to ~0) are repaired with `shapely.make_valid`, and a `note` says so.
4. Choose a projection centred on the feature (see below), project it, and read `area` / `length` in metres.
5. Any exception here becomes an `ERROR` result for that feature only, so one bad feature never fails the file.

### CRS handling

The rule from the brief is: never compute area or distance in lat/lon degrees. The strategy:

1. **Resolve the source CRS** per layer from the `.prj` / KML (KML is always EPSG:4326). If a shapefile has no `.prj`:
   - all coordinates within ±180 / ±90 → assume EPSG:4326 and add a warning to the file;
   - otherwise → `FAILED`, because guessing a CRS would give silently wrong numbers.
2. **Reproject the layer to WGS84** (vectorised, once per layer).
3. **Project each feature into a metric CRS centred on that feature** (the centre of its bounding box):
   - **Polygons → Lambert Azimuthal Equal-Area (LAEA).** An equal-area projection preserves area exactly, at any size and anywhere on Earth (including the poles).
   - **Lines → Transverse Mercator with scale factor 1 on the feature's own central meridian.** Distortion grows with the square of the distance from the centre, so it stays far below 0.01% for features up to a few hundred km.
4. Measure in metres. The `projected_crs` field in each result names the projection used, so every number is traceable.

Going through WGS84 first also fixes files that are already "projected" in a CRS that is bad for measuring: a polygon in Web Mercator (EPSG:3857) is ~1/cos²(lat) too large if measured directly, and the tests check that we return the true area instead. Even for a UTM file, the reported length is the true ground distance rather than the UTM grid distance (which differs by UTM's scale factor of ~0.9996–1.001): that is why the 2,500 m grid-length pipeline in the sample reports 2,500.297 m.

**Accuracy check.** The tests compare results with `pyproj.Geod` (Karney's geodesic algorithms on the WGS84 ellipsoid) as an independent reference: polygons in Bengaluru, London, the Arctic and a 1,100 km-wide polygon all agree within 0.001%.

---

## Design decisions

| Decision | Why | Alternatives considered |
|---|---|---|
| **FastAPI** | Typed request/response models, automatic OpenAPI docs at `/docs`, small amount of code. | Django + DRF: excellent for a large app with an admin, but heavier for a three-endpoint service. |
| **Per-feature centred projection** (LAEA for area, Transverse Mercator for length) | Equal-area gives exact areas; one rule works everywhere with no zone tables or polar special cases. | **UTM zone of the centroid**: the textbook answer, but UTM is conformal not equal-area, with up to ~0.1% length / ~0.2% area error near zone edges, features spanning zones, and no coverage near the poles. My first version used UTM; the geodesic tests showed a 0.1% area error in Bengaluru (2.5° from the zone's central meridian), which is why I switched. **Geodesic on the ellipsoid (`pyproj.Geod`)**: the most accurate, but the brief asks for a projected CRS, so I use it as the test reference instead. **One fixed equal-area CRS (e.g. EPSG:6933)**: simple, but distorts lengths and shapes far from its centre. |
| **Direct PROJ pipeline per feature** | Every feature has its own projection. `Transformer.from_crs` runs PROJ's operation search (~6 ms each); building the pipeline string directly skips that. A 2,000-polygon shapefile went from **16.5 s to 0.7 s**. | Rounding centres to share transformers (less accurate, more complex). |
| **GDAL via pyogrio + GeoPandas** | One battle-tested reader for Shapefile, KML and KMZ, including multi-layer files; wheels bundle GDAL so `pip install` just works. | `pyshp` + a hand-written KML parser: no binary dependency, but more code and many KML edge cases (MultiGeometry, folders, ExtendedData). Fiona: similar, but pyogrio is faster and GeoPandas' default. |
| **Read zips in place with `/vsizip/`** | No extraction means no zip-slip path traversal and no temp-file cleanup. Zip-bomb guard on total uncompressed size. | Extracting to a temp directory. |
| **Synchronous processing in the request** | Simple and enough for files of this size; the endpoint is a plain `def`, so FastAPI runs it in a thread pool and the event loop stays free. A `status` field already exists so the API won't need to change for async processing. | A task queue (Celery / RQ / FastAPI BackgroundTasks) with `PROCESSING` → `COMPLETED` polling. Listed under future scope. |
| **Measure once at upload, store results** | Reads are cheap and repeatable; the upload is the natural point to fail fast. | Computing on every `GET` (no storage, but repeated work). |
| **SQLite + SQLAlchemy** | Zero setup for a reviewer; switching to PostgreSQL is one environment variable. | PostGIS: would allow spatial queries, but is a heavy requirement for running an assignment locally. |
| **Graceful partial failure** | File-level problems → `FAILED` with a clear message (422). Feature-level problems → a status per feature (`NOT_APPLICABLE` / `UNSUPPORTED` / `ERROR`); the rest of the file is still measured. | Rejecting the whole file when any feature is unsupported. |
| **Repair invalid polygons** | Real-world data often has self-intersections; `make_valid` gives a meaningful area instead of ~0, and the `note` makes the repair visible. | Rejecting invalid polygons, or silently measuring them. |
| **Geometry stored as GeoJSON in the source CRS** | Users get back exactly what they uploaded; measurements are separate and reference the projection used. | Storing everything in EPSG:4326. |

---

## Testing

```bash
pytest -v
```

31 tests:

- **API (end to end)**: KML, KMZ and Shapefile uploads; file info; features and pagination; listing and deletion; unsupported geometries; self-intersecting polygons; Web Mercator and UTM inputs; missing `.prj` (assumed WGS84 vs. refused); zip without `.shp`; shapefile missing `.dbf`; corrupt KML; non-zip `.zip`; wrong extension (400); size limit (413); unknown id (404); measurements of a failed file (409).
- **Measurement accuracy**: areas and lengths compared with `pyproj.Geod` at several latitudes (including the Arctic) and sizes, within 0.001%.

---

## Learnings

- **Degrees are not distances.** One degree of longitude is ~111 km at the equator and ~0 at the poles, so area in "square degrees" is meaningless; everything has to go through a projection (or geodesic maths).
- **Every projection trades something away.** Conformal projections like UTM and Web Mercator keep shapes but not areas; equal-area projections keep areas but not shapes. Picking the projection based on *what you're measuring* mattered more than picking the "standard" one.
- **Test against an independent reference.** Comparing with `pyproj.Geod` exposed the 0.1% UTM area error that looked "close enough" by eye.
- **Profile before optimising.** The per-feature transformer looked harmless until a profiler showed 90% of the time in PROJ's operation search.
- **Real-world files are messy**: missing `.prj` files, macOS `__MACOSX` folders in zips, KML altitude values, empty KML layers, GDAL styling fields, self-intersecting polygons.

## Future scope

- **Asynchronous processing** with a task queue for large files (`status` + polling, or webhooks).
- **More formats**: GeoJSON, GeoPackage, GPX, CSV with WKT.
- **Geodesic mode** (`?method=geodesic`) using `pyproj.Geod` as an alternative to the projected method, and **unit selection** (`?unit=acres|hectares|miles`).
- **Antimeridian handling**: a feature crossing ±180° gets a wrongly centred bounding box today; split or normalise longitudes first.
- **GeometryCollection support** by measuring each polygonal / linear part.
- **PostGIS** for spatial queries (e.g. "features within this area") and **object storage** (S3) for uploads.
- **Auth, rate limiting and per-user file ownership**; retention policy for old uploads.
- **CI** (GitHub Actions running `pytest` and a linter) and structured logging / metrics.
