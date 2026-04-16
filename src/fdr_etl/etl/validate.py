import json
import logging
import os

from frictionless import Resource, Schema
from frictionless.errors import FrictionlessException
from frictionless.formats import SqlControl

logger = logging.getLogger(__name__)


def validate_file(filepath: str) -> bool:
    """
    Valide le fichier geopackage (SQLite) pour la couche 'eaupotable'
    en se basant sur le tableschema 'eaupotable.json'.
    """
    logger.info(f"Début de la validation pour: {filepath}")

    # Le chemin du schema (relatif au module courant)
    base_dir = os.path.dirname(os.path.dirname(__file__))
    schema_path = os.path.join(base_dir, "schemas", "eaupotable.json")

    try:




        # Chargement manuel du schéma
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_data = json.load(f)

        # Conversion du dictionnaire en objet Schema (Frictionless v5)
        schema_obj = Schema.from_descriptor(schema_data)

        # Un geopackage est une base de données SQLite.
        resource = Resource(
            path=f"sqlite:///{filepath}",
            control=SqlControl(table="eaupotable"),
            schema=schema_obj,
        )

        # Lancement de la validation
        report = resource.validate()

        if report.valid:
            logger.info("Succès, le fichier respecte le schéma.")
            return True
        else:
            logger.error("Echec de la validation:")
            # On affiche les erreurs
            for task in report.tasks:
                for error in task.errors:
                    logger.error(f"  - {error.message}")
            return False

    except FrictionlessException as e:
        logger.error(f"Erreur frictionless: {e}")
        return False
    except Exception as e:
        logger.exception(f"Erreur inattendue lors de la validation: {e}")
        return False
