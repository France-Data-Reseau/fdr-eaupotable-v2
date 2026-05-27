import logging
import sqlite3
import psycopg
from psycopg import sql
import uuid
import os

logger = logging.getLogger(__name__)

TABLES_TO_LOAD = {
    "eau_potable — aep_canalisation": {
        "pg_table": "aep_canalisation",
        "columns": ['fid', 'geom', 'type_reseau', 'fictif', 'etat_service', 'insee_commune', 'localisation', 'maitre_ouvrage', 'exploitant', 'entreprise_pose', 'precision_xy', 'precision_z', 'an_pose_sup', 'an_pose_inf', 'an_service_sup', 'an_service_inf', 'an_abandon_sup', 'an_abandon_inf', 'an_rehab_sup', 'an_rehab_inf', 'date_creation', 'origine_creation', 'date_maj', 'origine_maj', 'lien_doc1', 'lien_doc2', 'commentaire', 'id_canalisation', 'mode_circulation', 'type_pose', 'raison_pose', 'materiau', 'revetement_interieur', 'diametre_equivalent', 'longueur_terrain', 'sensible', 'noeudterminal', 'noeudinitial', 'forme', 'lien_detail', 'hauteur_interieure', 'hauteur_exterieure', 'largeur_interieure', 'largeur_exterieure', 'longueur_interieure', 'longueur_exterieure', 'id_aep_canalisation', 'fonction_canalisation', 'contenu_canalisation', 'protection_cathodique', 'etage_pression', 'type_pression', 'secteur_hydraulique', 'ref_udi', 'cote_debut', 'cote_fin', 'ref_reservoir']
    },
    "aep_perimetre": {
        "pg_table": "aep_perimetre",
        "columns": ['fid', 'geom', 'Id_perimetre', 'N° SIREN', "Nom de la collectivité de l'entité de gestion à laquelle la commune adhère", "Identifiant SISPEA de la collectivité de l'entité de gestion à laquelle la commune adhère", 'etat_service', 'type_perimetre_gestion']
    },
    "eau_potable — aep_reparation": {
        "pg_table": "aep_reparation",
        "columns": ['fid', 'geom', 'idReparation', 'supportIncident', 'dateIntervention', 'qualiteGeolocalisation', 'materiau', 'diametreNominal', 'datePose', 'emplacement', 'type', 'causeProbable']
    },
}
def extract_siren_from_gpkg(filepath: str) -> str:
    """Va lire le premier numéro SIREN valide trouvé dans la couche périmètre"""
    try:
        with sqlite3.connect(filepath) as sqlite_conn:
            cursor = sqlite_conn.cursor()
            # On récupère le premier SIREN non vide
            query = 'SELECT "N° SIREN" FROM "aep_perimetre" WHERE "N° SIREN" IS NOT NULL LIMIT 1;'
            cursor.execute(query)
            row = cursor.fetchone()
            if row and row[0]:
                return str(row[0]).strip()
    except Exception as e:
        logger.warning(f"⚠️ Impossible d'extraire le SIREN du GeoPackage : {e}")
    return None

def init_metadata_table(pg_cursor):
    """Création de la table des métadonnées si elle n'existe pas déjà"""
    create_query = """
    CREATE TABLE IF NOT EXISTS imports_metadata (
        file_id UUID PRIMARY KEY,
        collectivite_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT NOT NULL DEFAULT 'active' -- 'active' ou 'superseded'
    );
    """
    pg_cursor.execute(create_query)

def load_file_to_db(filepath: str, db_url: str, import_id: str, collectivite_id: str):
    """
    Charge les données d'un GeoPackage dans Postgres.
    Nettoie le binaire GeoPackage à la volée pour assurer la compatibilité PostGIS.
    """
    # On essaie d'extraire le SIREN du fichier
    gpkg_siren = extract_siren_from_gpkg(filepath)
    
    # S'il est trouvé, on l'utilise. Sinon, on garde la valeur transmise par l'API
    if gpkg_siren:
        collectivite_id = gpkg_siren
    filename = os.path.basename(filepath)
    logger.info(f"🚀 Début du chargement (ID: {import_id}) pour la collectivité : {collectivite_id}")

    # Initialisation globale de la table metadata et journalisation de l'import
    try:
        with psycopg.connect(db_url) as pg_conn:
            with pg_conn.cursor() as pg_cursor:
                # S'assurer que la table existe
                init_metadata_table(pg_cursor)
                
                # Avant d'activer le nouvel import, on passe les anciens en 'superseded'
                logger.info(f"🔄 Archivage des anciens imports pour la collectivité : {collectivite_id}")
                pg_cursor.execute(
                    """
                    UPDATE imports_metadata 
                    SET status = 'superseded' 
                    WHERE collectivite_id = %s AND status = 'active';
                    """,
                    [collectivite_id]
                )
                
                # Enregistrement du nouvel import qui sera 'active' par défaut
                logger.info(f"📝 Journalisation du nouvel import : {filename}")
                pg_cursor.execute(
                    """
                    INSERT INTO imports_metadata (file_id, collectivite_id, filename, status) 
                    VALUES (%s, %s, %s, 'active');
                    """,
                    [import_id, collectivite_id, filename]
                )
                pg_conn.commit()
    except Exception as e:
        logger.error(f"❌ Impossible d'initialiser les métadonnées de l'import : {e}")
        raise e

    for sqlite_table, info in TABLES_TO_LOAD.items():
        pg_table = info["pg_table"]
        columns = info["columns"]
        # Nom de table de staging unique pour éviter les collisions entre workers
        staging_table = f"stg_{pg_table}_{import_id.replace('-', '_')}"
        
        # Extraction depuis SQLite
        try:
            with sqlite3.connect(filepath) as sqlite_conn:
                cursor = sqlite_conn.cursor()
                cols_str = ", ".join([f'"{c}"' for c in columns])
                query = f'SELECT {cols_str} FROM "{sqlite_table}"'
                
                cursor.execute(query)
                rows = cursor.fetchall()
                logger.info(f"📦 {sqlite_table} : {len(rows)} lignes extraites.")
        except sqlite3.OperationalError as e:
            logger.error(f"❌ Erreur colonnes/table GPKG pour {sqlite_table} : {e}")
            continue

        if not rows:
            continue

        # Phase d'insertion Postgres
        try:
            with psycopg.connect(db_url) as pg_conn:
                with pg_conn.cursor() as pg_cursor:
                    
                    # Création de la table de STAGING (provisoire - stocke le binaire brut)
                    definitions_stg = []
                    for c in columns:
                        if c == 'geom':
                            definitions_stg.append(sql.SQL("{} BYTEA").format(sql.Identifier(c)))
                        else:
                            definitions_stg.append(sql.SQL("{} TEXT").format(sql.Identifier(c)))
                    
                    pg_cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(staging_table)))
                    pg_cursor.execute(sql.SQL("CREATE UNLOGGED TABLE {} ({})").format(
                        sql.Identifier(staging_table), 
                        sql.SQL(", ").join(definitions_stg)
                    ))

                    # Chargement rapide via COPY dans Staging
                    cols_identifiers = [sql.Identifier(c) for c in columns]
                    copy_query = sql.SQL("COPY {} ({}) FROM STDIN").format(
                        sql.Identifier(staging_table),
                        sql.SQL(', ').join(cols_identifiers)
                    )
                    
                    with pg_cursor.copy(copy_query) as copy:
                        for row in rows:
                            copy.write_row(row)

                    # Création de la table FINALE
                    definitions_final = []
                    for c in columns:
                        if c == 'geom':
                            definitions_final.append(sql.SQL("{} BYTEA").format(sql.Identifier(c)))
                        else:
                            definitions_final.append(sql.SQL("{} TEXT").format(sql.Identifier(c)))
                    definitions_final.append(sql.SQL("file_id TEXT"))

                    pg_cursor.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                        sql.Identifier(pg_table), 
                        sql.SQL(", ").join(definitions_final)
                    ))

                    # Transfert avec NETTOYAGE BINAIRE (Header GPKG) à la volée
                    select_fields = []
                    for c in columns:
                        if c == 'geom':
                            # Cette logique retire dynamiquement le header GPKG (8 à 73 octets)
                            # pour ne laisser que le WKB standard lisible par PostGIS
                            header_logic = sql.SQL("""
                                CASE 
                                    WHEN substring({} FROM 1 FOR 2) = '\\x4750' THEN 
                                        substring({} FROM (
                                            CASE (get_byte({}, 3) & 14) >> 1 
                                                WHEN 0 THEN 9 WHEN 1 THEN 41 WHEN 2 THEN 57 
                                                WHEN 3 THEN 57 WHEN 4 THEN 73 ELSE 9 
                                            END
                                        ))
                                    ELSE {} 
                                END
                            """).format(sql.Identifier(c), sql.Identifier(c), sql.Identifier(c), sql.Identifier(c))
                            select_fields.append(header_logic)
                        else:
                            select_fields.append(sql.Identifier(c))

                    insert_query = sql.SQL("""
                        INSERT INTO {} ({}, file_id) 
                        SELECT {}, %s FROM {}
                    """).format(
                        sql.Identifier(pg_table),
                        sql.SQL(", ").join(cols_identifiers),
                        sql.SQL(", ").join(select_fields),
                        sql.Identifier(staging_table)
                    )
                    
                    pg_cursor.execute(insert_query, [import_id])

                    # Suppression de la table temporaire de staging
                    pg_cursor.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(staging_table)))
                    
                    logger.info(f"✅ {pg_table} : Transfert finalisé ({len(rows)} lignes).")

        except Exception as e:
            logger.error(f"❌ Erreur Postgres ({pg_table}): {e}")
            # Nettoyage de secours en cas d'échec
            try:
                with psycopg.connect(db_url) as conn_err:
                    with conn_err.cursor() as cur_err:
                        cur_err.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(staging_table)))
            except: pass
            raise e

    return import_id