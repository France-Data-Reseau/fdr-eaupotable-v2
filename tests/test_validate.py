import sqlite3

import pytest

from fdr_etl.etl.validate import validate_file


@pytest.fixture
def temp_gpkg(tmp_path):
    """Crée un chemin pour un fichier SQLite temporaire."""
    db_path = tmp_path / "test.gpkg"
    return str(db_path)


def test_validate_file_success(temp_gpkg):
    """Teste la validation réussie d'un Geopackage conforme."""
    conn = sqlite3.connect(temp_gpkg)
    conn.execute(
        "CREATE TABLE eaupotable (fid INTEGER, A NUMERIC, B NUMERIC, C NUMERIC)"
    )
    conn.execute("INSERT INTO eaupotable (fid, A, B, C) VALUES (1, 10.5, 20, 30.2)")
    conn.commit()
    conn.close()

    assert validate_file(temp_gpkg) is True


def test_validate_file_wrong_columns(temp_gpkg):
    """Teste l'échec si une colonne (ex: C) est manquante."""
    conn = sqlite3.connect(temp_gpkg)
    conn.execute("CREATE TABLE eaupotable (fid INTEGER, A NUMERIC, B NUMERIC)")
    conn.commit()
    conn.close()

    assert validate_file(temp_gpkg) is False


def test_validate_file_wrong_types(temp_gpkg):
    """
    Teste l'échec si une colonne contient un type invalide
    (string au lieu de number).
    """
    conn = sqlite3.connect(temp_gpkg)
    conn.execute(
        "CREATE TABLE eaupotable (fid INTEGER, A NUMERIC, B NUMERIC, C NUMERIC)"
    )
    # On insère une chaîne de caractères dans la colonne A qui doit être un nombre
    conn.execute("INSERT INTO eaupotable (fid, A, B, C) VALUES (1, 'invalide', 20, 30)")
    conn.commit()
    conn.close()

    assert validate_file(temp_gpkg) is False


def test_validate_file_missing_table(temp_gpkg):
    """Teste l'échec si la table 'eaupotable' est absente du fichier."""
    conn = sqlite3.connect(temp_gpkg)
    conn.execute("CREATE TABLE une_autre_table (id INTEGER)")
    conn.commit()
    conn.close()

    assert validate_file(temp_gpkg) is False


def test_validate_file_not_exists():
    """Teste l'échec si le fichier n'existe pas."""
    assert validate_file("chemin/inexistant.gpkg") is False
