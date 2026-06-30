import psycopg


def get_db_connection(database_url: str):
    """Returns a psycopg connection to the database."""
    return psycopg.connect(database_url)
