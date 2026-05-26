import os
import json
import sqlite3
import logging
from frictionless import Resource, Schema

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
SCHEMA_DIR = os.path.join(BASE_DIR, "schemas")

PIPELINE_CONFIG = {
    "eau_potable — aep_canalisation": "canalisation",
    "aep_perimetre": "perimetre",
    "eau_potable — aep_reparation": "reparation"
}

def validate_file(filepath: str):
    """
    Valide le GeoPackage et retourne un dictionnaire détaillé des erreurs.
    Identifie les colonnes manquantes ET les erreurs de données simultanément.
    """
    results = {"valid": True, "errors": []}
    
    if not os.path.exists(filepath):
        results["valid"] = False
        results["errors"].append({"table": "Système", "message": "Fichier introuvable sur le serveur."})
        return results

    abs_gpkg_path = os.path.abspath(filepath)
    
    for table_name, schema_file in PIPELINE_CONFIG.items():
        schema_path = os.path.join(SCHEMA_DIR, f"{schema_file}.json")
        
        if not os.path.exists(schema_path):
            continue

        try:
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema_data = json.load(f)
            schema_obj = Schema.from_descriptor(schema_data)
            
            # --- Diagnostic de la structure (Colonnes) ---
            target_columns = [field['name'] for field in schema_data.get('fields', [])]
            
            conn = sqlite3.connect(abs_gpkg_path)
            cursor = conn.cursor()
            
            # Vérifier l'existence de la table
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
            if not cursor.fetchone():
                conn.close()
                continue

            # Lister les colonnes réelles pour identifier celles qui manquent
            cursor.execute(f'PRAGMA table_info("{table_name}")')
            actual_columns = [info[1] for info in cursor.fetchall()]
            
            missing_cols = [c for c in target_columns if c not in actual_columns]
            if missing_cols:
                results["valid"] = False
                for col in missing_cols:
                    results["errors"].append({
                        "table": table_name,
                        "message": f"Structure incorrecte : La colonne **{col}** est manquante dans la table."
                    })

            # --- Extraction des données  ---
            # On ne sélectionne que ce qui existe pour éviter que le SELECT ne plante
            available_cols = [c for c in target_columns if c in actual_columns]
            if not available_cols:
                conn.close()
                continue

            cols_query = ", ".join([f'"{c}"' for c in available_cols])
            
            conn.row_factory = lambda cursor, row: dict(zip(
                [col[0] for col in cursor.description], 
                [str(val) if val is not None else None for val in row]
            ))
            
            cursor = conn.cursor()
            cursor.execute(f'SELECT {cols_query} FROM "{table_name}"')
            rows = cursor.fetchall()
            conn.close()
            
            # --- Validation du contenu avec Frictionless ---
            resource = Resource(data=rows, schema=schema_obj)
            report = resource.validate()

            if not report.valid:
                results["valid"] = False
                summary = {}
                
                for error in report.tasks[0].errors:
                    # on ignore les erreurs de labels Frictionless car gérées manuellement
                    if hasattr(error, 'code') and error.code in ['label-error', 'missing-label']:
                        continue

                    field = getattr(error, 'field_name', 'Structure/Général')
                    val = getattr(error, 'cell', 'N/A')
                    
                    if hasattr(error, 'code') and error.code:
                        err_type = error.code.replace('-', ' ').capitalize()
                    else:
                        err_type = "Erreur de format"
                    
                    if field not in summary:
                        summary[field] = {}
                    if err_type not in summary[field]:
                        summary[field][err_type] = {"count": 0, "values": []}
                    
                    summary[field][err_type]["count"] += 1
                    
                    repr_val = f"'{val}'" if val is not None else "VIDE"
                    if repr_val not in summary[field][err_type]["values"] and len(summary[field][err_type]["values"]) < 5:
                        summary[field][err_type]["values"].append(repr_val)

                for field, types in summary.items():
                    for err_type, info in types.items():
                        sample = ", ".join(info['values'])
                        msg = f"Champ **{field}** : {info['count']} erreur(s) de type '{err_type}'."
                        if info['values']:
                            msg += f" (Exemples de valeurs non valides : {sample})"
                        
                        results["errors"].append({
                            "table": table_name,
                            "message": msg
                        })

        except sqlite3.OperationalError as e:
            results["valid"] = False
            results["errors"].append({
                "table": table_name, 
                "message": f"Erreur de lecture SQL : {str(e)}"
            })
        except Exception as e:
            results["valid"] = False
            # Protection contre l'erreur .code manquante
            error_msg = str(e) if str(e) else "Erreur de traitement inconnue"
            results["errors"].append({"table": table_name, "message": error_msg})

    return results