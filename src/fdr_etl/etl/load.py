import logging
import sqlite3

import psycopg

logger = logging.getLogger(__name__)


def load_file_to_db(filepath: str, db_url: str):
    """
    Charge les données brutes issues de la couche 'eaupotable' du Geopackage
    vers la base PostgreSQL.
    """
    logger.info(f"Chargement des données de {filepath} en base de données.")

    # 1. Lecture des données depuis le Geopackage (SQLite)
    sqlite_conn = sqlite3.connect(filepath)
    sqlite_cursor = sqlite_conn.cursor()

    try:
        sqlite_cursor.execute("SELECT A, B, C FROM eaupotable")
        rows = sqlite_cursor.fetchall()
        logger.info(f"-> {len(rows)} lignes lues depuis le fichier geopackage.")
    except Exception as e:
        logger.exception(f"Erreur lors de la lecture SQLite: {e}")
        sqlite_conn.close()
        raise e

    sqlite_conn.close()

    # 2. Insertion des données dans PostgreSQL
    try:
        with psycopg.connect(db_url) as pg_conn:
            with pg_conn.cursor() as pg_cursor:
                # Création de la table si elle n'existe pas
                # (A, B, C sont de type INTEGER/NUMERIC)
                pg_cursor.execute("""
                    CREATE TABLE IF NOT EXISTS eaupotable (
                        id SERIAL PRIMARY KEY,
                        A NUMERIC,
                        B NUMERIC,
                        C NUMERIC
                    )
                """)

                # Insertion rapide avec copy
                if rows:
                    with pg_cursor.copy("COPY eaupotable (A, B, C) FROM STDIN") as copy:
                        for row in rows:
                            copy.write_row(row)
                    logger.info(
                        f"Intégration de {len(rows)} lignes validée dans Postgres."
                    )

            # Commit implicite en sortant du bloc 'with pg_conn'
    except Exception as e:
        logger.exception(f"Erreur lors de l'intégration Postgres: {e}")
        raise e
