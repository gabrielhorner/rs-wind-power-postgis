"""
01_carga.py - Load the three source datasets into PostGIS.

Reads:
  1. ANEEL/SIGEL wind power plants (shapefile)      -> table wind_plants
  2. MMA/CNUC protected areas (shapefile)           -> table protected_areas
  3. IBGE BC250 municipal seats (GeoPackage layer)   -> table municipal_seats

Keeps only Rio Grande do Sul (RS). Wind plants are filtered to the phases
"Operação" (operating) and "Construção não iniciada" (granted, not yet built).
All layers stay in SIRGAS 2000 (EPSG:4674).

Run from the project folder, with the virtual environment active:
    python scripts\\01_carga.py
"""

import os
import getpass
from pathlib import Path

import geopandas as gpd
from shapely.geometry import MultiPolygon
from sqlalchemy import create_engine, text

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_DIR / "data" / "raw"

WIND_SHP = RAW_DIR / "Central_Geradora_Eólica_-_EOL.shp"
CNUC_SHP = RAW_DIR / "cnuc_2025_08.shp"
# The BC250 GeoPackage (~1.5 GB) is too large for the repository, so it stays
# outside the project. Change the path with the BC250_GPKG environment variable.
BC250_GPKG = Path(os.environ.get(
    "BC250_GPKG",
    Path.home() / "Downloads" / "projetosql" / "bc250_gpkg_2026-03-03" / "bc250_2026-03-03.gpkg",
))
SEATS_LAYER = "lml_cidade_p"

SRID = 4674  # SIRGAS 2000, geographic
PHASES = ["Operação", "Construção não iniciada"]

DB_HOST = os.environ.get("PGHOST", "localhost")
DB_PORT = os.environ.get("PGPORT", "5432")
DB_NAME = os.environ.get("PGDATABASE", "eolicas_rs")
DB_USER = os.environ.get("PGUSER", "postgres")


def get_engine():
    """Connect to PostgreSQL. The password is never stored in the code:
    it comes from the PGPASSWORD environment variable or is typed at run time."""
    password = os.environ.get("PGPASSWORD") or getpass.getpass(
        f"Password for PostgreSQL user '{DB_USER}': "
    )
    url = f"postgresql+psycopg2://{DB_USER}:{password}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    return create_engine(url)


# ---------------------------------------------------------------------------
# 1. Wind power plants (ANEEL/SIGEL)
# ---------------------------------------------------------------------------
def load_wind_plants():
    gdf = gpd.read_file(WIND_SHP)
    print(f"[wind] {len(gdf)} plants in Brazil")

    gdf = gdf[gdf["UF1"] == "RS"]
    print(f"[wind] {len(gdf)} plants in RS (all phases)")

    gdf = gdf[gdf["FASE"].isin(PHASES)].copy()
    print(f"[wind] {len(gdf)} plants in RS after phase filter {PHASES}")

    # Keep the useful columns and give them clear English names
    gdf = gdf.rename(columns={
        "Nome": "name",
        "CEG": "ceg",
        "CODMUN": "muni_code",
        "MUNIC1": "municipality",
        "FASE": "phase",
        "P_OUT_KW": "power_granted_kw",
        "P_FISC_KW": "power_inspected_kw",
        "QTD_AEG": "n_turbines",
        "INICIO_OPE": "operation_start",
        "PROPRIETAR": "owner",
        "DATA_ATUAL": "source_updated",
    })[[
        "ceg", "name", "muni_code", "municipality", "phase",
        "power_granted_kw", "power_inspected_kw", "n_turbines",
        "operation_start", "owner", "source_updated", "geometry",
    ]]
    # IBGE municipality code as text (7 digits), same format as the seats layer
    gdf["muni_code"] = gdf["muni_code"].astype("Int64").astype(str)

    if gdf["ceg"].duplicated().any():
        raise ValueError("Duplicated CEG codes after filtering - check the source data")

    return gdf.to_crs(SRID)


# ---------------------------------------------------------------------------
# 2. Protected areas (MMA/CNUC)
# ---------------------------------------------------------------------------
def fix_text(value):
    """The CNUC .dbf says UTF-8, but at least one value was cut in the middle of
    an accented character. Reading as latin1 never fails; then we re-decode as
    UTF-8 and replace only the broken bytes."""
    if isinstance(value, str):
        return value.encode("latin1").decode("utf-8", errors="replace")
    return value


def load_protected_areas():
    gdf = gpd.read_file(CNUC_SHP, encoding="latin1")
    text_cols = gdf.columns[gdf.dtypes.astype(str).isin(["object", "str", "string"])]
    for col in text_cols:
        gdf[col] = gdf[col].map(fix_text)
    print(f"[cnuc] {len(gdf)} protected areas in Brazil")

    # The 'uf' field lists every state the area touches, e.g. "RIO GRANDE DO SUL, SANTA CATARINA"
    gdf = gdf[gdf["uf"].str.contains("RIO GRANDE DO SUL", na=False)].copy()
    print(f"[cnuc] {len(gdf)} protected areas touching RS")

    gdf = gdf[[
        "cd_cnuc", "nome_uc", "esfera", "grupo", "categoria",
        "quali_pol", "ha_total", "uf", "geometry",
    ]].rename(columns={
        "nome_uc": "name",
        "esfera": "sphere",
        "grupo": "uc_group",
        "categoria": "category",
        "quali_pol": "polygon_quality",
        "ha_total": "area_ha",
    })

    # One geometry type for the whole table: Polygon -> MultiPolygon
    gdf["geometry"] = [
        MultiPolygon([geom]) if geom.geom_type == "Polygon" else geom
        for geom in gdf.geometry.make_valid()
    ]
    return gdf.to_crs(SRID)


# ---------------------------------------------------------------------------
# 3. Municipal seats (IBGE BC250)
# ---------------------------------------------------------------------------
def load_municipal_seats():
    if not BC250_GPKG.exists():
        raise FileNotFoundError(f"BC250 GeoPackage not found: {BC250_GPKG}")

    # Read only RS seats (IBGE codes starting with 43) straight from the
    # 1.5 GB file, instead of loading the whole country into memory.
    gdf = gpd.read_file(
        BC250_GPKG,
        layer=SEATS_LAYER,
        where="CAST(geocodigo AS TEXT) LIKE '43%'",
    )
    print(f"[seats] {len(gdf)} municipal seats in RS")

    gdf = gdf.rename(columns={"nome": "name", "geocodigo": "muni_code"})
    gdf["muni_code"] = gdf["muni_code"].astype(str)

    dup = gdf["muni_code"].duplicated(keep=False)
    if dup.any():
        print(f"[seats] WARNING: {dup.sum()} rows share a municipality code:")
        print(gdf.loc[dup, ["muni_code", "name"]].sort_values("muni_code").to_string(index=False))

    return gdf[["muni_code", "name", "geometry"]].to_crs(SRID)


# ---------------------------------------------------------------------------
# Data fix: 6-digit municipality codes in the ANEEL data
# ---------------------------------------------------------------------------
def fix_muni_codes(wind, seats):
    """IBGE municipality codes have 7 digits: 6 digits + 1 check digit.
    A few ANEEL records store only the first 6 digits (e.g. Rio Grande as
    431560 instead of 4315602), so they would not join with the IBGE seats.
    The 6-digit prefix is unique, so we complete it from the seats table."""
    by_prefix = dict(zip(seats["muni_code"].str[:6], seats["muni_code"]))
    short = wind["muni_code"].str.len() == 6
    if short.any():
        print(f"[fix] {short.sum()} plants with 6-digit municipality code:")
        print(wind.loc[short, ["ceg", "name", "municipality", "muni_code"]].to_string(index=False))
        wind.loc[short, "muni_code"] = wind.loc[short, "muni_code"].map(by_prefix)

    missing = ~wind["muni_code"].isin(seats["muni_code"])
    if missing.any():
        raise ValueError(
            "Plants without a matching municipal seat:\n"
            + wind.loc[missing, ["ceg", "name", "municipality", "muni_code"]].to_string(index=False)
        )
    print(f"[fix] all {len(wind)} plants now match a municipal seat")
    return wind


# ---------------------------------------------------------------------------
# Write to PostGIS
# ---------------------------------------------------------------------------
def write_table(gdf, table, engine, key=None):
    # if_exists="replace" makes the script safe to run again from scratch.
    # GeoAlchemy2 also creates a GiST spatial index on the geometry column.
    gdf.rename_geometry("geom").to_postgis(table, engine, if_exists="replace", index=False)
    with engine.begin() as conn:
        if key:
            conn.execute(text(f'ALTER TABLE {table} ADD PRIMARY KEY ("{key}")'))
        n = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()
        srid = conn.execute(text(f"SELECT Find_SRID('public', '{table}', 'geom')")).scalar()
    print(f"[db] {table}: {n} rows, SRID {srid}")


def main():
    wind = load_wind_plants()
    areas = load_protected_areas()
    seats = load_municipal_seats()
    wind = fix_muni_codes(wind, seats)

    engine = get_engine()
    write_table(wind, "wind_plants", engine, key="ceg")
    write_table(areas, "protected_areas", engine, key="cd_cnuc")
    write_table(seats, "municipal_seats", engine)
    print("Done.")


if __name__ == "__main__":
    main()
