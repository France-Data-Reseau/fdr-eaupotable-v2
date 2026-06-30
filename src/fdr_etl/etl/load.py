import logging
import sqlite3
import psycopg
from psycopg import sql
import uuid
import os
import struct

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

def strip_gpkg_header(blob: bytes) -> bytes:
    """
    Supprime le header GeoPackage (spec OGC §2.1.3) d'un blob WKB.
    Gère tous les cas de flags : envelope type, empty geometry, SRS ID étendu.
    Retourne le WKB pur, ou le blob original s'il n'est pas un GPKG.
    """
    if blob is None or len(blob) < 8:
        return blob
    # Magic bytes 'GP' (0x47, 0x50)
    if blob[0] != 0x47 or blob[1] != 0x50:
        return blob

    flags = blob[3]
    envelope_type = (flags & 0b00001110) >> 1  # bits 1-3
    is_empty      = (flags & 0b00010000) >> 4  # bit 4
    has_ext_srs   = (flags & 0b00100000) >> 5  # bit 5 (Extended SRS ID)

    envelope_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    envelope_size = envelope_sizes.get(envelope_type, 0)

    # Header de base : 8 octets
    # + 4 octets si SRS ID étendu (has_ext_srs)
    # + taille de l'enveloppe
    header_size = 8 + (4 if has_ext_srs else 0) + envelope_size

    if len(blob) <= header_size:
        return blob  # Géométrie vide ou tronquée

    return blob[header_size:]

def extract_siren_from_gpkg(filepath: str) -> str:
    """Va lire le premier numéro SIREN valide trouvé dans la couche périmètre"""
    try:
        with sqlite3.connect(filepath) as sqlite_conn:
            cursor = sqlite_conn.cursor()
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
        status TEXT NOT NULL DEFAULT 'active',
        permissivity_level INTEGER NOT NULL DEFAULT 1
    );
    """
    pg_cursor.execute(create_query)
    # Rétrocompatibilité : ajout de la colonne si la table existait avant cette version
    pg_cursor.execute(
        "ALTER TABLE imports_metadata ADD COLUMN IF NOT EXISTS permissivity_level INTEGER NOT NULL DEFAULT 1;"
    )

def load_file_to_db(
    filepath: str,
    db_url: str,
    import_id: str,
    collectivite_id: str,
    permissivity_level: int,
    columns_to_skip: dict = None):
    """
    Charge les données d'un GeoPackage dans Postgres.
    Le header binaire GPKG est strippé en Python (strip_gpkg_header) avant
    insertion, ce qui garantit la compatibilité PostGIS quel que soit le
    type d'enveloppe ou le flag SRS ID étendu du fichier source.

    La colonne `permissivity_level` est ajoutée à chaque table de données
    (aep_canalisation, aep_perimetre, aep_reparation) ainsi qu'à imports_metadata.
    Elle permet à transform.py de filtrer les données éligibles aux stats globales
    (niveaux 1 et 2) ou locales uniquement (niveau 3).

    Paramètres
    ----------
    permissivity_level : int
        Niveau accordé par validate_file() : 1, 2 ou 3. (Requis)
    columns_to_skip : dict
        {sqlite_table_name: [col_name, ...]} — colonnes optionnelles invalides
        ou absentes à ne pas charger (issues du rapport de validation).
    """
    if columns_to_skip is None:
        columns_to_skip = {}

    # On essaie d'extraire le SIREN du fichier
    gpkg_siren = extract_siren_from_gpkg(filepath)
    if gpkg_siren:
        collectivite_id = gpkg_siren

    filename = os.path.basename(filepath)
    logger.info(
        f"🚀 Début du chargement (ID: {import_id}, niveau de permissivité: {permissivity_level}) "
        f"pour la collectivité : {collectivite_id}"
    )

    # ── Initialisation des métadonnées ───────────────────────────────────────
    try:
        with psycopg.connect(db_url) as pg_conn:
            with pg_conn.cursor() as pg_cursor:
                init_metadata_table(pg_cursor)

                logger.info(f"🔄 Archivage des anciens imports pour la collectivité : {collectivite_id}")
                pg_cursor.execute(
                    """
                    UPDATE imports_metadata
                    SET status = 'superseded'
                    WHERE collectivite_id = %s AND status = 'active';
                    """,
                    [collectivite_id],
                )

                logger.info(f"📝 Journalisation du nouvel import : {filename}")
                pg_cursor.execute(
                    """
                    INSERT INTO imports_metadata (file_id, collectivite_id, filename, status, permissivity_level)
                    VALUES (%s, %s, %s, 'active', %s);
                    """,
                    [import_id, collectivite_id, filename, permissivity_level],
                )
                pg_conn.commit()
    except Exception as e:
        logger.error(f"❌ Impossible d'initialiser les métadonnées de l'import : {e}")
        raise e

    # ── Chargement de chaque table ───────────────────────────────────────────
    for sqlite_table, info in TABLES_TO_LOAD.items():
        pg_table = info["pg_table"]
        all_columns = info["columns"]

        # Exclure les colonnes optionnelles invalides/absentes signalées par validate_file
        skip_set = set(columns_to_skip.get(sqlite_table, []))
        columns = [c for c in all_columns if c not in skip_set]

        if skip_set:
            logger.info(
                f"⚠️  [{sqlite_table}] Colonnes optionnelles exclues du chargement : {sorted(skip_set)}"
            )

        staging_table = f"stg_{pg_table}_{import_id.replace('-', '_')}"

        # ── Extraction depuis SQLite ─────────────────────────────────────────
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

        # ── Stripping du header GPKG en Python ──────────────────────────────
        geom_idx = columns.index('geom') if 'geom' in columns else None
        if geom_idx is not None:
            stripped_count = 0
            cleaned_rows = []
            for row in rows:
                row = list(row)
                if isinstance(row[geom_idx], bytes):
                    original_len = len(row[geom_idx])
                    row[geom_idx] = strip_gpkg_header(row[geom_idx])
                    if len(row[geom_idx]) != original_len:
                        stripped_count += 1
                cleaned_rows.append(tuple(row))
            rows = cleaned_rows
            logger.info(
                f"🧹 [{sqlite_table}] Header GPKG strippé sur {stripped_count}/{len(rows)} géométrie(s)."
            )

        # ── Phase d'insertion Postgres ───────────────────────────────────────
        try:
            with psycopg.connect(db_url) as pg_conn:
                with pg_conn.cursor() as pg_cursor:

                    # ── Table de STAGING ─────────────────────────────────────
                    definitions_stg = []
                    for c in columns:
                        if c == 'geom':
                            definitions_stg.append(sql.SQL("{} BYTEA").format(sql.Identifier(c)))
                        else:
                            definitions_stg.append(sql.SQL("{} TEXT").format(sql.Identifier(c)))

                    pg_cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(staging_table)))
                    pg_cursor.execute(sql.SQL("CREATE UNLOGGED TABLE {} ({})").format(
                        sql.Identifier(staging_table),
                        sql.SQL(", ").join(definitions_stg),
                    ))

                    cols_identifiers = [sql.Identifier(c) for c in columns]
                    copy_query = sql.SQL("COPY {} ({}) FROM STDIN").format(
                        sql.Identifier(staging_table),
                        sql.SQL(", ").join(cols_identifiers),
                    )
                    with pg_cursor.copy(copy_query) as copy:
                        for row in rows:
                            copy.write_row(row)

                    # ── Table FINALE ─────────────────────────────────────────
                    definitions_final = []
                    for c in columns:
                        if c == 'geom':
                            definitions_final.append(sql.SQL("{} BYTEA").format(sql.Identifier(c)))
                        else:
                            definitions_final.append(sql.SQL("{} TEXT").format(sql.Identifier(c)))

                    # Colonnes de traçabilité
                    definitions_final.append(sql.SQL("file_id TEXT"))
                    definitions_final.append(sql.SQL("permissivity_level INTEGER"))

                    pg_cursor.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                        sql.Identifier(pg_table),
                        sql.SQL(", ").join(definitions_final),
                    ))

                    # Rétrocompatibilité : colonnes ajoutées si table déjà existante
                    pg_cursor.execute(
                        sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS file_id TEXT").format(
                            sql.Identifier(pg_table)
                        )
                    )
                    pg_cursor.execute(
                        sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS permissivity_level INTEGER").format(
                            sql.Identifier(pg_table)
                        )
                    )

                    # Colonnes optionnelles ignorées dans ce chargement :
                    # on les crée quand même (NULL) pour ne pas casser les autres imports
                    for c in skip_set:
                        pg_cursor.execute(
                            sql.SQL("ALTER TABLE {} ADD COLUMN IF NOT EXISTS {} TEXT").format(
                                sql.Identifier(pg_table),
                                sql.Identifier(c),
                            )
                        )

                    # ── Transfert staging → table finale (sans logique GPKG SQL) ──
                    select_fields = [sql.Identifier(c) for c in columns]

                    insert_query = sql.SQL("""
                        INSERT INTO {} ({}, file_id, permissivity_level)
                        SELECT {}, %s, %s FROM {}
                    """).format(
                        sql.Identifier(pg_table),
                        sql.SQL(", ").join(cols_identifiers),
                        sql.SQL(", ").join(select_fields),
                        sql.Identifier(staging_table),
                    )
                    pg_cursor.execute(insert_query, [import_id, permissivity_level])

                    pg_cursor.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(staging_table)))

                    logger.info(f"✅ {pg_table} : Transfert finalisé ({len(rows)} lignes, niveau {permissivity_level}).")

        except Exception as e:
            logger.error(f"❌ Erreur Postgres ({pg_table}): {e}")
            try:
                with psycopg.connect(db_url) as conn_err:
                    with conn_err.cursor() as cur_err:
                        cur_err.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(staging_table)))
            except Exception:
                pass
            raise e

    return import_id