-- =============================================================================
-- 02_analise.sql - Spatial analysis of wind power plants in Rio Grande do Sul
--
-- Input tables (created by scripts/01_carga.py, all in SIRGAS 2000, EPSG:4674):
--   wind_plants       134 plants (phases: operating + granted, not yet built)
--   municipal_seats   497 municipal seats (IBGE BC250)
--   protected_areas   101 protected areas touching RS (MMA/CNUC)
--
-- Distances are computed on the ellipsoid with the geography type (results in
-- metres), because RS spans two UTM zones (21S and 22S).
--
-- The script can be run again from scratch: every result table is dropped and
-- recreated.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 0. Data check: does every plant have a seat for its municipality code?
--    Expected result: 0 rows.
-- -----------------------------------------------------------------------------
SELECT p.ceg, p.name, p.municipality, p.muni_code
FROM wind_plants p
LEFT JOIN municipal_seats s ON s.muni_code = p.muni_code
WHERE s.muni_code IS NULL;


-- -----------------------------------------------------------------------------
-- 1 + 2. Distance from each plant to
--    (a) the seat of its own municipality (attribute join on the IBGE code)
--    (b) the nearest seat of any municipality (KNN search with the <-> operator)
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS plant_seat_distance;

CREATE TABLE plant_seat_distance AS
SELECT
    p.ceg,
    p.name,
    p.municipality,
    p.phase,
    p.power_granted_kw,
    -- (a) own municipality seat
    own.name                                                AS own_seat,
    ROUND((ST_Distance(p.geom::geography, own.geom::geography) / 1000)::numeric, 2)
                                                            AS own_seat_km,
    -- (b) nearest seat of any municipality
    near.name                                               AS nearest_seat,
    near.muni_code                                          AS nearest_seat_code,
    ROUND((near.dist_m / 1000)::numeric, 2)                 AS nearest_seat_km,
    (near.muni_code <> p.muni_code)                         AS nearest_is_other_municipality
FROM wind_plants p
LEFT JOIN municipal_seats own
       ON own.muni_code = p.muni_code
CROSS JOIN LATERAL (
    -- Step 1: the spatial index (GiST) finds the 5 closest seats fast, using
    --         planar distance in degrees.
    -- Step 2: among those 5, pick the closest by true geodesic distance.
    --         Degrees are not metres (1 degree of longitude is shorter than
    --         1 degree of latitude in RS), so the planar ranking alone could
    --         pick the wrong seat when two seats are at similar distances.
    SELECT c.name, c.muni_code,
           ST_Distance(p.geom::geography, c.geom::geography) AS dist_m
    FROM (
        SELECT s.name, s.muni_code, s.geom
        FROM municipal_seats s
        ORDER BY s.geom <-> p.geom
        LIMIT 5
    ) c
    ORDER BY dist_m
    LIMIT 1
) near;

ALTER TABLE plant_seat_distance ADD PRIMARY KEY (ceg);

-- Plants that are closer to a neighbouring town than to their own seat
SELECT name, municipality, own_seat_km, nearest_seat, nearest_seat_km
FROM plant_seat_distance
WHERE nearest_is_other_municipality
ORDER BY own_seat_km - nearest_seat_km DESC;

-- Summary by phase
SELECT phase,
       COUNT(*)                                   AS plants,
       ROUND(AVG(own_seat_km), 1)                 AS avg_own_seat_km,
       ROUND(MIN(own_seat_km), 1)                 AS min_own_seat_km,
       ROUND(MAX(own_seat_km), 1)                 AS max_own_seat_km,
       SUM(nearest_is_other_municipality::int)    AS closer_to_other_town
FROM plant_seat_distance
GROUP BY phase;


-- -----------------------------------------------------------------------------
-- 3. Buffers of 2 km and 10 km around each plant (for the map)
--
--    2 km  - CONAMA Resolution 428/2010, art. 5, III (as amended by CONAMA
--            508/2025): the protected-area manager must be notified when a
--            project lies within 2,000 m of a protected area whose buffer zone
--            has not been established (not applicable to APAs, RPPNs and
--            consolidated urban areas).
--    10 km - former CONAMA Resolution 13/1990 (revoked in 2010): 10 km
--            "surrounding area" around protected areas.
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS plant_buffers;

CREATE TABLE plant_buffers AS
SELECT p.ceg,
       r.radius_m,
       -- Buffer built on the ellipsoid (geography), stored back as geometry 4674
       ST_Buffer(p.geom::geography, r.radius_m)::geometry(Polygon, 4674) AS geom
FROM wind_plants p
CROSS JOIN (VALUES (2000), (10000)) AS r(radius_m);

ALTER TABLE plant_buffers ADD PRIMARY KEY (ceg, radius_m);
CREATE INDEX plant_buffers_geom_idx ON plant_buffers USING GIST (geom);


-- -----------------------------------------------------------------------------
-- 4. Plants within 2 km / 10 km of a protected area
--
--    ST_DWithin on geography tests the exact distance on the ellipsoid. It is
--    more precise than intersecting the buffer polygons above (which are
--    approximated by straight segments) and it can use the spatial index.
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS plant_protected_area;

CREATE TABLE plant_protected_area AS
SELECT
    p.ceg,
    p.name                                   AS plant,
    p.municipality,
    p.phase,
    a.cd_cnuc,
    a.name                                   AS protected_area,
    a.category,
    a.uc_group,
    a.sphere,
    a.polygon_quality,
    ROUND((ST_Distance(p.geom::geography, a.geom::geography) / 1000)::numeric, 2)
                                             AS distance_km,
    ST_Intersects(p.geom, a.geom)            AS plant_inside_area,
    -- The 2 km rule does not apply to APAs and RPPNs (CONAMA 508/2025)
    (a.category NOT IN ('Área de Proteção Ambiental',
                        'Reserva Particular do Patrimônio Natural'))
                                             AS rule_2km_applies
FROM wind_plants p
JOIN protected_areas a
  ON ST_DWithin(p.geom::geography, a.geom::geography, 10000);

ALTER TABLE plant_protected_area ADD PRIMARY KEY (ceg, cd_cnuc);

-- Main result: how many plants are near a protected area, at each radius
SELECT
    COUNT(DISTINCT ceg) FILTER (WHERE plant_inside_area)                       AS inside_area,
    COUNT(DISTINCT ceg) FILTER (WHERE distance_km <= 2)                        AS within_2km_any,
    COUNT(DISTINCT ceg) FILTER (WHERE distance_km <= 2 AND rule_2km_applies)   AS within_2km_rule_applies,
    COUNT(DISTINCT ceg)                                                        AS within_10km,
    (SELECT COUNT(*) FROM wind_plants)                                         AS total_plants
FROM plant_protected_area;

-- Same result, split by phase
SELECT phase,
       COUNT(DISTINCT ceg) FILTER (WHERE distance_km <= 2)  AS within_2km,
       COUNT(DISTINCT ceg)                                  AS within_10km
FROM plant_protected_area
GROUP BY phase;

-- Which protected areas have the most plants nearby (10 km)
SELECT protected_area, category, uc_group, sphere,
       COUNT(*)                                       AS plants_within_10km,
       COUNT(*) FILTER (WHERE distance_km <= 2)       AS plants_within_2km,
       MIN(distance_km)                               AS closest_km
FROM plant_protected_area
GROUP BY protected_area, category, uc_group, sphere
ORDER BY plants_within_10km DESC;
