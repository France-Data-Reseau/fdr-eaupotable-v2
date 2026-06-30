def get_geom_cast_query(table_name: str, geom_type: str, import_id_str: str, target_srid: int = 2154) -> list[str]:
    """
    Retourne une liste de requêtes SQL à exécuter séquentiellement.
    psycopg n'accepte pas plusieurs statements dans un seul cur.execute(),
    donc on sépare chaque étape dans sa propre chaîne.

    Étapes :
        1. Cast BYTEA → geometry (sans SRID)
        2. Création de la table de quarantaine
        3. Mise en quarantaine des géométries invalides
        4. Nullification des géométries invalides
        5. Projection adaptative GPS (4326) ou projetée (target_srid)
        6. Typage strict de la colonne finale
    """
    return [
        # Étape 1 : Cast BYTEA → geometry brute
        f"""
        ALTER TABLE {table_name} ALTER COLUMN geom TYPE geometry
            USING CASE
                WHEN geom IS NOT NULL THEN ST_GeomFromWKB(geom)
                ELSE NULL
            END;
        """,

        # Étape 2 : Table de quarantaine (idempotente)
        """
        CREATE TABLE IF NOT EXISTS geom_quarantine (
            table_name  TEXT,
            fid         TEXT,
            file_id     TEXT,
            reason      TEXT,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,

        # Étape 3 : Mise en quarantaine des géométries invalides
        f"""
        INSERT INTO geom_quarantine (table_name, fid, file_id, reason)
        SELECT
            '{table_name}',
            fid::text,
            file_id,
            ST_IsValidReason(geom)
        FROM {table_name}
        WHERE file_id = '{import_id_str}'
          AND geom IS NOT NULL
          AND NOT ST_IsValid(geom);
        """,

        # Étape 4 : Nullification des géométries invalides
        f"""
        UPDATE {table_name}
        SET geom = NULL
        WHERE file_id = '{import_id_str}'
          AND geom IS NOT NULL
          AND NOT ST_IsValid(geom);
        """,

        # Étape 5 : Projection adaptative
        f"""
        UPDATE {table_name}
        SET geom = CASE
            WHEN ST_XMin(geom) BETWEEN -180.0 AND 180.0
                 AND ST_XMax(geom) BETWEEN -180.0 AND 180.0
                 AND ST_YMin(geom) BETWEEN  -90.0 AND  90.0
                 AND ST_YMax(geom) BETWEEN  -90.0 AND  90.0
            THEN ST_Transform(ST_SetSRID(geom, 4326), {target_srid})
            ELSE ST_SetSRID(geom, {target_srid})
        END
        WHERE file_id = '{import_id_str}'
          AND geom IS NOT NULL;
        """,

        # Étape 6 : Typage strict de la colonne finale
        f"""
        ALTER TABLE {table_name} ALTER COLUMN geom TYPE geometry({geom_type}, {target_srid})
            USING geom::geometry({geom_type}, {target_srid});
        """,
    ]

def get_transformation_queries(import_id_str: str) -> dict:
    """
    Retourne le dictionnaire des requêtes SQL de nettoyage ciblées par file_id.
    """
    return {
        "Calcul de la date de pose consolidée": f"""
            UPDATE aep_canalisation
            SET date_pose = CASE 
                WHEN NULLIF(an_pose_sup, '') IS NOT NULL 
                    AND NULLIF(an_pose_inf, '') IS NOT NULL 
                    AND an_pose_sup ~ '^[0-9]+$' 
                    AND an_pose_inf ~ '^[0-9]+$'
                    AND an_pose_sup::int >= an_pose_inf::int 
                    THEN ((an_pose_sup::int + an_pose_inf::int) / 2)
                WHEN NULLIF(an_pose_inf, '') IS NOT NULL AND an_pose_inf ~ '^[0-9]+$' THEN an_pose_inf::int
                WHEN NULLIF(an_pose_sup, '') IS NOT NULL AND an_pose_sup ~ '^[0-9]+$' THEN an_pose_sup::int
                ELSE NULL
            END
            WHERE file_id = '{import_id_str}';

            UPDATE aep_canalisation
            SET date_pose = NULL
            WHERE (date_pose < 1700 OR date_pose > EXTRACT(YEAR FROM CURRENT_DATE))
              AND file_id = '{import_id_str}';
        """,

        "Standardisation Numérique des Diamètres": f"""
            UPDATE aep_canalisation 
            SET diametre_num = NULLIF(REGEXP_REPLACE(diametre_equivalent, '[^0-9]', '', 'g'), '')::INTEGER
            WHERE file_id = '{import_id_str}';
        """,

        "Classification Diamètres": f"""
            UPDATE aep_canalisation SET dia_ens = CASE
                WHEN diametre_num > 0 AND diametre_num < 64 THEN ']0;64['
                WHEN diametre_num >= 64 AND diametre_num < 100 THEN '[64;100['
                WHEN diametre_num >= 100 THEN '[100+['
                ELSE 'Indéterminé'
            END
            WHERE file_id = '{import_id_str}';
        """,

        "Classification Périodes de Pose": f"""
            UPDATE aep_canalisation SET ddp_ens = CASE
                WHEN date_pose::text ~ '^\d+$' AND date_pose::int < 1900 THEN ']-1900['
                WHEN date_pose::text ~ '^\d+$' AND date_pose::int >= 1900 AND date_pose::int < 1930 THEN '[1900;1930['
                WHEN date_pose::text ~ '^\d+$' AND date_pose::int >= 1930 AND date_pose::int < 1960 THEN '[1930;1960['
                WHEN date_pose::text ~ '^\d+$' AND date_pose::int >= 1960 AND date_pose::int < 1990 THEN '[1960;1990['
                WHEN date_pose::text ~ '^\d+$' AND date_pose::int >= 1990 THEN '[1990+['
                ELSE 'Indéterminée'
            END
            WHERE file_id = '{import_id_str}';
        """,

        "Jointure Spatiale - Étape 1/3 : Calcul des correspondances": f"""
            DROP TABLE IF EXISTS tmp_jointure_reparation;
            
            ANALYZE aep_canalisation;
            ANALYZE aep_reparation;

            CREATE UNLOGGED TABLE tmp_jointure_reparation AS
            SELECT 
                r.fid as rid, 
                c.id_aep_canalisation as cid,
                c.materiau as c_materiau
            FROM aep_reparation r
            LEFT JOIN LATERAL (
                SELECT id_aep_canalisation, materiau
                FROM aep_canalisation
                WHERE file_id = '{import_id_str}'          
                  AND geom IS NOT NULL
                  -- Utilisation de l'opérateur de Box && combiné à ST_DWithin pour forcer l'index
                  AND geom && ST_Expand(r.geom, 5.0)
                  AND ST_DWithin(geom, r.geom, 5.0)             
                ORDER BY geom <-> r.geom
                LIMIT 1
            ) c ON TRUE
            WHERE r.file_id = '{import_id_str}'
              AND r.geom IS NOT NULL;
        """,

        "Jointure Spatiale - Étape 2/3 : Indexation de la Table Temp": f"""
            CREATE INDEX idx_tmp_jointure_rid ON tmp_jointure_reparation(rid);
        """,

        "Jointure Spatiale - Étape 3/3 : Application de l'UPDATE final": f"""
            UPDATE aep_reparation r
            SET 
                supportincident_auto = CASE 
                    WHEN r."supportIncident" IS NULL 
                         OR TRIM(r."supportIncident") = '' 
                         OR LOWER(TRIM(r."supportIncident")) IN ('none', 'null', 'nan')
                    THEN link.cid 
                    ELSE TRIM(r."supportIncident") 
                END,
                
                materiau = CASE 
                    WHEN (r.materiau IS NULL OR TRIM(r.materiau) = '' OR r.materiau ILIKE 'indetermine%')
                    THEN COALESCE(NULLIF(TRIM(link.c_materiau), ''), 'Indéterminé')
                    ELSE r.materiau
                END
            FROM tmp_jointure_reparation link
            WHERE r.fid = link.rid
              AND r.file_id = '{import_id_str}';
              
            DROP TABLE IF EXISTS tmp_jointure_reparation;
        """
    }