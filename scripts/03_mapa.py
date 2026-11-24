"""
03_mapa.py - Build the final map and export the result tables.

Reads the tables created by sql/02_analise.sql from PostGIS and writes:
  output/map_wind_plants_rs.png          final map
  output/plant_seat_distance.csv         distance from each plant to municipal seats
  output/plant_protected_area.csv        plant / protected-area pairs within 10 km

The municipal boundaries (background only) are read straight from the IBGE
BC250 GeoPackage, like the seats in 01_carga.py.

Run from the project folder, with the virtual environment active:
    python scripts\\03_mapa.py
"""

import os
import sys
import math
import getpass
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import matplotlib
matplotlib.use("Agg")  # draw to file, no window needed
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from sqlalchemy import create_engine

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_DIR / "output"
BC250_GPKG = Path(os.environ.get(
    "BC250_GPKG",
    Path.home() / "Downloads" / "projetosql" / "bc250_gpkg_2026-03-03" / "bc250_2026-03-03.gpkg",
))
MUNICIPALITY_LAYER = "lml_municipio_a"

# Colours (validated categorical palette: blue, orange, aqua) and neutral inks
C_OPERATING = "#2a78d6"
C_NOT_BUILT = "#eb6834"
C_AREA = "#1baf7a"
C_BOUNDARY = "#b8b7b1"
C_TEXT = "#0b0b0b"
C_TEXT_2 = "#52514e"
C_SURFACE = "#fcfcfb"

DB_HOST = os.environ.get("PGHOST", "localhost")
DB_PORT = os.environ.get("PGPORT", "5432")
DB_NAME = os.environ.get("PGDATABASE", "eolicas_rs")
DB_USER = os.environ.get("PGUSER", "postgres")


def get_engine():
    password = os.environ.get("PGPASSWORD") or getpass.getpass(
        f"Password for PostgreSQL user '{DB_USER}': "
    )
    return create_engine(f"postgresql+psycopg2://{DB_USER}:{password}@{DB_HOST}:{DB_PORT}/{DB_NAME}")


def read_municipalities():
    """RS municipal boundaries (IBGE codes starting with 43), for the background."""
    layers = [name for name, _ in pyogrio.list_layers(BC250_GPKG)]
    if MUNICIPALITY_LAYER not in layers:
        sys.exit(
            f"Layer '{MUNICIPALITY_LAYER}' not found in {BC250_GPKG.name}.\n"
            f"Layers with 'munic' in the name: {[l for l in layers if 'munic' in l]}"
        )
    gdf = gpd.read_file(BC250_GPKG, layer=MUNICIPALITY_LAYER,
                        where="CAST(geocodigo AS TEXT) LIKE '43%'")
    print(f"[map] {len(gdf)} RS municipalities read for the background")
    return gdf.to_crs(4674)


def main():
    OUT_DIR.mkdir(exist_ok=True)
    engine = get_engine()

    # --- Result tables from PostGIS ------------------------------------------
    plants = gpd.read_postgis(
        """SELECT p.ceg, p.name, p.phase, p.geom,
                  EXISTS (SELECT 1 FROM plant_protected_area a WHERE a.ceg = p.ceg) AS near_area
           FROM wind_plants p""",
        engine, geom_col="geom")
    areas = gpd.read_postgis("SELECT name, uc_group, geom FROM protected_areas", engine, geom_col="geom")
    buffers = gpd.read_postgis(
        "SELECT ceg, geom FROM plant_buffers WHERE radius_m = 10000", engine, geom_col="geom")

    distance = pd.read_sql("SELECT * FROM plant_seat_distance ORDER BY municipality, name", engine)
    pairs = pd.read_sql("SELECT * FROM plant_protected_area ORDER BY distance_km", engine)
    distance.to_csv(OUT_DIR / "plant_seat_distance.csv", index=False, encoding="utf-8-sig")
    pairs.to_csv(OUT_DIR / "plant_protected_area.csv", index=False, encoding="utf-8-sig")
    print(f"[csv] {len(distance)} rows -> output/plant_seat_distance.csv")
    print(f"[csv] {len(pairs)} rows -> output/plant_protected_area.csv")

    municipalities = read_municipalities()
    state = municipalities.dissolve()

    # --- Map -----------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(10, 9.5), facecolor=C_SURFACE)
    ax.set_facecolor(C_SURFACE)

    municipalities.boundary.plot(ax=ax, color=C_BOUNDARY, linewidth=0.25, zorder=1)
    state.boundary.plot(ax=ax, color=C_TEXT_2, linewidth=0.8, zorder=2)

    # Protected areas: full-protection with hatch, sustainable use plain
    full = areas["uc_group"] == "Proteção Integral"
    areas[~full].plot(ax=ax, color=C_AREA, alpha=0.35, linewidth=0, zorder=3)
    areas[full].plot(ax=ax, facecolor=C_AREA, alpha=0.6, edgecolor=C_SURFACE,
                     hatch="////", linewidth=0, zorder=3)

    # 10 km buffers, outline only
    buffers.boundary.plot(ax=ax, color=C_TEXT_2, linewidth=0.4, linestyle=(0, (2, 2)), zorder=4)

    # Plants by phase; a dark ring marks plants within 10 km of a protected area
    for phase, colour in [("Operação", C_OPERATING), ("Construção não iniciada", C_NOT_BUILT)]:
        sel = plants[plants["phase"] == phase]
        sel[~sel["near_area"]].plot(ax=ax, color=colour, markersize=22,
                                    edgecolor=C_SURFACE, linewidth=0.8, zorder=5)
        sel[sel["near_area"]].plot(ax=ax, color=colour, markersize=30,
                                   edgecolor=C_TEXT, linewidth=1.2, zorder=6)

    # Frame on the state, small margin
    xmin, ymin, xmax, ymax = state.total_bounds
    ax.set_xlim(xmin - 0.2, xmax + 0.2)
    ax.set_ylim(ymin - 0.2, ymax + 0.2)
    # Degrees of longitude are shorter than degrees of latitude at this latitude:
    # correct the aspect so the state is not stretched sideways.
    ax.set_aspect(1 / math.cos(math.radians((ymin + ymax) / 2)))
    ax.set_xticks([])
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    n_total = len(plants)
    n_near = int(plants["near_area"].sum())
    n_op = int((plants["phase"] == "Operação").sum())
    n_nb = n_total - n_op
    ax.set_title(
        "Wind power plants in Rio Grande do Sul and protected areas",
        loc="left", fontsize=15, color=C_TEXT, pad=26, fontweight="bold")
    ax.text(0, 1.015,
            f"{n_near} of {n_total} plants lie within 10 km of a protected area "
            f"(none within 2 km of one where the 2 km rule applies)",
            transform=ax.transAxes, fontsize=10.5, color=C_TEXT_2)

    legend_items = [
        Line2D([], [], marker="o", linestyle="", markersize=7, color=C_OPERATING,
               markeredgecolor=C_SURFACE, label=f"Operating ({n_op})"),
        Line2D([], [], marker="o", linestyle="", markersize=7, color=C_NOT_BUILT,
               markeredgecolor=C_SURFACE, label=f"Granted, not yet built ({n_nb})"),
        Line2D([], [], marker="o", linestyle="", markersize=7, color="none",
               markeredgecolor=C_TEXT, markeredgewidth=1.2,
               label=f"Within 10 km of a protected area ({n_near})"),
        Line2D([], [], color=C_TEXT_2, linewidth=0.8, linestyle=(0, (2, 2)), label="10 km buffer"),
        Patch(facecolor=C_AREA, alpha=0.6, hatch="////", edgecolor=C_SURFACE,
              label="Protected area - full protection"),
        Patch(facecolor=C_AREA, alpha=0.35, label="Protected area - sustainable use"),
    ]
    leg = ax.legend(handles=legend_items, loc="lower left", fontsize=9, frameon=True,
                    facecolor=C_SURFACE, edgecolor=C_BOUNDARY, labelcolor=C_TEXT)
    leg.get_frame().set_linewidth(0.6)

    fig.text(0.02, 0.015,
             "Sources: ANEEL/SIGEL (wind plants, phases operating and granted), MMA/CNUC 2025-08 "
             "(protected areas), IBGE BC250 2025 (boundaries).\n"
             "CRS: SIRGAS 2000 (EPSG:4674). Distances computed on the ellipsoid (PostGIS geography).",
             fontsize=7.5, color=C_TEXT_2)

    fig.tight_layout(rect=(0, 0.04, 1, 1))
    out = OUT_DIR / "map_wind_plants_rs.png"
    fig.savefig(out, dpi=200, facecolor=C_SURFACE)
    print(f"[map] saved {out}")


if __name__ == "__main__":
    main()
