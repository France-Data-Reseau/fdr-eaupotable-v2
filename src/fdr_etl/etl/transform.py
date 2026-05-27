import logging
import uuid
import pandas as pd
import psycopg
import time
from sqlalchemy import create_engine, text

from fdr_etl.etl.sql_queries import get_transformation_queries
from fdr_etl.etl.stats_engine import execute_statistical_pipeline
from fdr_etl.etl.bootstrap import compute_weighted_bootstrap
from fdr_etl.etl.sql_queries import get_geom_cast_query

logger = logging.getLogger(__name__)

def get_observation_period(engine, import_id_str: str = None) -> float:
    """Calcule la durée de l'historique des casses."""
    nb_annees = 1.0
    try:
        with engine.connect() as connection:
            if import_id_str:
                query_period = text("""
                    SELECT (MAX("dateIntervention"::date) - MIN("dateIntervention"::date))::float / 365.25
                    FROM aep_reparation
                    WHERE "dateIntervention" IS NOT NULL 
                      AND "dateIntervention" != '' 
                      AND file_id = :import_id
                """)
                res = connection.execute(query_period, {"import_id": import_id_str}).fetchone()
            else:
                query_period = text("""
                    SELECT (MAX(r."dateIntervention"::date) - MIN(r."dateIntervention"::date))::float / 365.25
                    FROM aep_reparation r
                    INNER JOIN imports_metadata m ON r.file_id = m.file_id::text
                    WHERE r."dateIntervention" IS NOT NULL 
                      AND r."dateIntervention" != '' 
                      AND m.status = 'active'
                """)
                res = connection.execute(query_period).fetchone()
                
            if res and res[0] is not None:
                nb_annees = max(float(res[0]), 1.0)
    except Exception as e:
        logger.warning(f"⚠️ Erreur calcul période d'observation : {e}")
    return nb_annees

def run_transformations(db_url: str, import_id: str):
    """Orchestrateur principal : Transformations, Calcul local et Recalcul Global."""
    import_id_uuid = uuid.UUID(import_id) if isinstance(import_id, str) else import_id
    import_id_str = str(import_id_uuid)

    try:
        # =========================================================
        # PRÉPARATION DE LA STRUCTURE & INDEXATION (Anti-Lock)
        # =========================================================
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                logger.info("🔧 [Structure] Initialisation de l'extension et des colonnes...")
                cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
                
                # Ajout des colonnes structurelles (Validé immédiatement pour libérer les verrous)
                cur.execute("ALTER TABLE aep_canalisation ADD COLUMN IF NOT EXISTS date_pose INTEGER;")
                cur.execute("ALTER TABLE aep_canalisation ADD COLUMN IF NOT EXISTS diametre_num INTEGER;")
                cur.execute("ALTER TABLE aep_canalisation DROP COLUMN IF EXISTS dia_ens;")
                cur.execute("ALTER TABLE aep_canalisation ADD COLUMN dia_ens TEXT;")
                cur.execute("ALTER TABLE aep_canalisation DROP COLUMN IF EXISTS ddp_ens;")
                cur.execute("ALTER TABLE aep_canalisation ADD COLUMN ddp_ens TEXT;")
                cur.execute("ALTER TABLE aep_reparation ADD COLUMN IF NOT EXISTS supportincident_auto TEXT;")
                
                # Création des index standard (B-Tree) sur les colonnes file_id (qui sont de type TEXT)
                logger.info("⚡ [Index] Création des index B-Tree sur file_id (TEXT)...")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_aep_canalisation_file_id ON aep_canalisation (file_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_aep_reparation_file_id ON aep_reparation (file_id);")
                conn.commit()

        # =========================================================
        # PROJECTIONS GÉOMÉTRIQUES ET RE-INDEXATION SPATIALE
        # =========================================================
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                logger.info("📐 [Géom] Vérification des géométries")
                cur.execute(get_geom_cast_query("aep_canalisation", "LineString", import_id_str))
                cur.execute(get_geom_cast_query("aep_reparation", "Point", import_id_str))
                conn.commit()

        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                logger.info("⚡ [Index] Régénération des index spatiaux GIST...")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_cana_geom ON aep_canalisation USING GIST(geom);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_rep_geom ON aep_reparation USING GIST(geom);")
                conn.commit()

        # =========================================================
        # EXÉCUTION DES TRANSFORMATION DE DONNÉES
        # =========================================================
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                logger.info("🚀 Lancement des transformations SQL (Nettoyage, Regex, Jointures)...")
                queries = get_transformation_queries(import_id_str)
                for desc, q in queries.items():
                    logger.info(f"⏳ {desc}...")
                    cur.execute(q)
                
                logger.info("⚡ [Index] Création de l'index sur la nouvelle colonne de liaison...")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_rep_support_auto ON aep_reparation(supportincident_auto);")
                conn.commit()

        # Préparation Engine SQLAlchemy
        engine_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1) if db_url.startswith("postgresql://") else db_url
        engine = create_engine(engine_url)

        # Récupération du Nom de la collectivité courante
        nom_coll = "Inconnue"
        try:
            with engine.connect() as connection:
                query_nom = text("""
                    SELECT "Nom de la collectivité de l'entité de gestion à laquelle la commune adhère" 
                    FROM aep_perimetre 
                    WHERE file_id::text = :import_id LIMIT 1
                """)
                result = connection.execute(query_nom, {"import_id": import_id_str}).fetchone()
                if result and result[0]:
                    nom_coll = str(result[0])
        except Exception: 
            pass

        # =========================================================
        # EXTRACTION ET CALCULS : VISION INDIVIDUELLE
        # =========================================================
        nb_annees_indiv = get_observation_period(engine, import_id_str)
        logger.info(f"📅 Période INDIVIDUELLE : {nb_annees_indiv:.1f} ans")

        query_indiv = text("""
            WITH current_reparations AS (
                SELECT supportincident_auto, COUNT(*) as cnt 
                FROM aep_reparation 
                WHERE file_id = :import_id
                  AND supportincident_auto IS NOT NULL
                GROUP BY supportincident_auto
            )
            SELECT 
                c.date_pose, 
                c.materiau, 
                c.dia_ens, 
                c.ddp_ens, 
                ST_Length(c.geom) / 1000.0 as longueur_km,
                COALESCE(r.cnt, 0) as nb_casses
            FROM aep_canalisation c 
            LEFT JOIN current_reparations r ON r.supportincident_auto = c.id_aep_canalisation
            WHERE c.file_id = :import_id
              AND c.type_reseau = 'aep'
              AND (c.fictif IS NULL OR LOWER(TRIM(c.fictif)) NOT IN ('1', 'true', 'vrai'))
              AND c.etat_service = 'en_service'
              AND (c.diametre_num IS NULL OR c.diametre_num > 25);
        """)
        start_load = time.time()
        logger.info(f"[{nom_coll}] Début du téléchargement SQL...")

        chunks = []
        for chunk in pd.read_sql(query_indiv, engine, params={"import_id": import_id_str}, chunksize=25000):
            chunks.append(chunk)

        df_raw_indiv = pd.concat(chunks, ignore_index=True)
        logger.info(f"[{nom_coll}] Extraction SQL de {len(df_raw_indiv)} lignes réussie en {time.time() - start_load:.3f}s")
        
        # Récupération du triplet incluant le dictionnaire d'indicateurs globaux
        df_patrimoine_indiv, df_croise_indiv, metadata_indiv = execute_statistical_pipeline(df_raw_indiv, nom_coll, nb_annees_indiv)

        # =========================================================
        # INJECTION DES DONNÉES DU FICHIER ENTRANT
        # =========================================================
        logger.info("💾 Écriture des statistiques individuelles et des données croisées...")
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                
                # SÉCURITÉ : Assurer que les colonnes existent dans la table imports_metadata
                cur.execute("ALTER TABLE imports_metadata ADD COLUMN IF NOT EXISTS lineaire_total FLOAT;")
                cur.execute("ALTER TABLE imports_metadata ADD COLUMN IF NOT EXISTS periode_obs FLOAT;")
                cur.execute("ALTER TABLE imports_metadata ADD COLUMN IF NOT EXISTS age_moyen FLOAT;")
                cur.execute("ALTER TABLE imports_metadata ADD COLUMN IF NOT EXISTS taux_global FLOAT;")
                cur.execute("ALTER TABLE imports_metadata ADD COLUMN IF NOT EXISTS taux_renouv FLOAT;")
                cur.execute("ALTER TABLE imports_metadata ADD COLUMN IF NOT EXISTS mat_dominant TEXT;")
                
                # Injection des données spécifiques au file_id dans imports_metadata
                cur.execute("""
                    UPDATE imports_metadata 
                    SET lineaire_total = %s, 
                        periode_obs = %s, 
                        age_moyen = %s, 
                        taux_global = %s, 
                        taux_renouv = %s,
                        mat_dominant = %s
                    WHERE file_id = %s;
                """, (
                    metadata_indiv['lineaire_total'],
                    metadata_indiv['periode_obs'],
                    metadata_indiv['age_moyen'],
                    metadata_indiv['taux_global'],
                    metadata_indiv['taux_renouv'],
                    metadata_indiv['mat_dominant'],
                    import_id_uuid
                ))

                # Structure allégée pour stats_patrimoine (sans pollution globale)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS stats_patrimoine (
                        analyse_type TEXT, categorie TEXT, km FLOAT, nom_collectivite TEXT, 
                        taux_moyen FLOAT, ic_inf FLOAT, ic_sup FLOAT, scope TEXT, file_id UUID
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS stats_croisees (
                        materiau TEXT, dia_ens TEXT, ddp_ens TEXT, km FLOAT, nb_casses INT, file_id UUID
                    );
                """)

                cur.execute("DELETE FROM stats_patrimoine WHERE file_id = %s AND scope = 'INDIVIDUAL';", [import_id_uuid])
                cur.execute("DELETE FROM stats_croisees WHERE file_id = %s;", [import_id_uuid])

                # Insertion dans stats_croisees
                with cur.copy("COPY stats_croisees (materiau, dia_ens, ddp_ens, km, nb_casses, file_id) FROM STDIN") as copy:
                    for _, row in df_croise_indiv.iterrows():
                        copy.write_row((row.materiau, row.dia_ens, row.ddp_ens, row.longueur_km, int(row.nb_casses), import_id_uuid))

                # Insertion allégée dans stats_patrimoine
                with cur.copy("COPY stats_patrimoine (analyse_type, categorie, km, nom_collectivite, taux_moyen, ic_inf, ic_sup, scope, file_id) FROM STDIN") as copy:
                    for _, row in df_patrimoine_indiv.iterrows():
                        copy.write_row((
                            row.analyse_type, 
                            row.categorie, 
                            float(row.km) if pd.notna(row.km) else 0.0, 
                            row.nom_collectivite, 
                            float(row.taux_moyen) if pd.notna(row.taux_moyen) else 0.0, 
                            float(row.ic_inf) if pd.notna(row.ic_inf) else 0.0, 
                            float(row.ic_sup) if pd.notna(row.ic_sup) else 0.0, 
                            'INDIVIDUAL', 
                            import_id_uuid
                        ))
                conn.commit()
                logger.info("🎯 Métadonnées globales et statistiques par catégorie mises à jour avec succès !")
        
        logger.info("✨ Pipeline modulaire, incrémental et consolidé terminé avec succès !")

    except Exception as e:
        logger.error(f"❌ Erreur lors du pipeline : {e}")
        raise e