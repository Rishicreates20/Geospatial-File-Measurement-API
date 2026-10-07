"""Generate the sample Shapefile zips in samples/. Run: python scripts/make_sample_shapefiles.py"""

import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def write_zip(layers: dict[str, gpd.GeoDataFrame], name: str) -> Path:
    """Write each GeoDataFrame as its own shapefile, all inside one zip."""
    target = SAMPLES / f"{name}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        for layer_name, gdf in layers.items():
            gdf.to_file(Path(tmp) / f"{layer_name}.shp", engine="pyogrio")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for part in sorted(Path(tmp).iterdir()):
                archive.write(part, part.name)
    return target


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)

    # Farm plots in WGS84 (EPSG:4326): the API must reproject before measuring.
    plots = gpd.GeoDataFrame(
        {"plot_id": ["A1", "A2"], "owner": ["Rao", "Iyer"]},
        geometry=[
            Polygon([(77.50, 13.10), (77.51, 13.10), (77.51, 13.11), (77.50, 13.11)]),
            Polygon([(77.52, 13.10), (77.525, 13.10), (77.525, 13.105), (77.52, 13.105)]),
        ],
        crs="EPSG:4326",
    )
    print("wrote", write_zip({"farm_plots": plots}, "farm_plots_wgs84"))

    # Survey data already in a projected CRS (UTM 43N, EPSG:32643). A shapefile holds a
    # single geometry type, so lines and points are two shapefiles in the same zip.
    lines = gpd.GeoDataFrame(
        {"name": ["Pipeline", "Fence"]},
        geometry=[
            LineString([(650000, 1440000), (651000, 1440000), (651000, 1441500)]),  # 2500 m
            LineString([(652000, 1440000), (652300, 1440400)]),  # 500 m
        ],
        crs="EPSG:32643",
    )
    benchmarks = gpd.GeoDataFrame({"name": ["BM-1"]}, geometry=[Point(650500, 1440500)], crs="EPSG:32643")
    print("wrote", write_zip({"survey_lines": lines, "benchmarks": benchmarks}, "survey_utm43n"))


if __name__ == "__main__":
    main()
