import os
from frictionless import Resource
from frictionless.errors import FrictionlessException


def validate_file(filepath: str) -> bool:
    """
    Valide le fichier geopackage (SQLite) pour la couche 'eaupotable'
    en se basant sur le tableschema 'eaupotable.json'.
    """
    print(f"[Validation] Début de la validation pour: {filepath}")

    # Le chemin du schema (relatif au module courant)
    base_dir = os.path.dirname(os.path.dirname(__file__))
    schema_path = os.path.join(base_dir, "schemas", "eaupotable.json")

    try:
        from frictionless.formats import SqlControl
        from frictionless import Schema
        import json

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
            print("[Validation] Succès, le fichier respecte le schéma.")
            return True
        else:
            print("[Validation] Echec de la validation:")
            # On affiche les erreurs
            for task in report.tasks:
                for error in task.errors:
                    print(f"  - {error.message}")
            return False

    except FrictionlessException as e:
        print(f"[Validation] Erreur frictionless: {e}")
        return False
    except Exception as e:
        print(f"[Validation] Erreur inattendue: {e}")
        return False
