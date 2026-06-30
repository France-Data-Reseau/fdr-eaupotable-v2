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

# Colonnes strictement nécessaires pour que transform.py fonctionne.
# Leur absence ou un type invalide bloque le pipeline (niveaux 2 et 3 refusés).
REQUIRED_COLUMNS_FOR_TRANSFORM = {
    "eau_potable — aep_canalisation": ['fid', 'geom', 'type_reseau', 'fictif', 'etat_service', 'insee_commune', 'precision_xy', 'precision_z', 'an_pose_sup', 'an_pose_inf', 'an_abandon_sup', 'an_abandon_inf','materiau', 'diametre_equivalent', 'forme', 'id_aep_canalisation'],
    "aep_perimetre": ['fid', 'geom', 'Id_perimetre', 'N° SIREN', "Nom de la collectivité de l'entité de gestion à laquelle la commune adhère", "Identifiant SISPEA de la collectivité de l'entité de gestion à laquelle la commune adhère", 'etat_service', 'type_perimetre_gestion'],
    "eau_potable — aep_reparation": ['fid', 'geom', 'idReparation', 'supportIncident', 'dateIntervention', 'qualiteGeolocalisation', 'materiau', 'diametreNominal', 'datePose', 'emplacement', 'type', 'causeProbable'],
}

def validate_file(filepath: str) -> dict:
    """
    Valide le GeoPackage et retourne un rapport enrichi avec le niveau de
    permissivité accordé.

    Niveaux retournés dans results["level"] :
        1 — Schéma complet respecté (toutes tables, toutes colonnes, tous types).
            Données éligibles aux statistiques globales.
        2 — Colonnes minimales OK + toutes les colonnes optionnelles sont présentes
            mais certaines ont des erreurs de type (exclues du chargement).
            Données éligibles aux statistiques globales.
        3 — Colonnes minimales OK mais des colonnes optionnelles sont structurellement
            absentes du fichier.
            Données chargées mais marquées incomplètes ; statistiques locales uniquement.
        None — Au moins une colonne requise pour transform est absente ou invalide.
                Pipeline bloqué.

    results["valid"] : True pour niveaux 1/2/3, False pour None.

    results["columns_to_skip"] : dict {sqlite_table_name: [col, ...]}
        Colonnes optionnelles invalides ou absentes à exclure du chargement.
        Vide pour le niveau 1.
    """
    results = {
        "valid": True,
        "level": 1,
        "errors": [],       # erreurs bloquantes (colonnes requises)
        "warnings": [],     # avertissements non-bloquants (colonnes optionnelles)
        "columns_to_skip": {},
    }

    if not os.path.exists(filepath):
        results["valid"] = False
        results["level"] = None
        results["errors"].append({
            "table": "Système",
            "message": "Fichier introuvable sur le serveur.",
        })
        return results

    abs_gpkg_path = os.path.abspath(filepath)

    # Flags pour déterminer le niveau en fin de boucle
    has_blocking_issues = False       # colonne requise absente ou invalide → rejection
    has_missing_optional = False      # colonne optionnelle structurellement absente → niveau 3
    has_invalid_optional = False      # colonne optionnelle présente mais type invalide → niveau 2

    for table_name, schema_file in PIPELINE_CONFIG.items():
        schema_path = os.path.join(SCHEMA_DIR, f"{schema_file}.json")
        if not os.path.exists(schema_path):
            continue

        required_cols = set(REQUIRED_COLUMNS_FOR_TRANSFORM.get(table_name, []))

        try:
            with open(schema_path, "r", encoding="utf-8") as f:
                schema_data = json.load(f)
            schema_obj = Schema.from_descriptor(schema_data)
            target_columns = [field["name"] for field in schema_data.get("fields", [])]

            # ── Existence de la table ────────────────────────────────────────
            conn = sqlite3.connect(abs_gpkg_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            table_exists = cursor.fetchone()
            conn.close()

            if not table_exists:
                # Toutes les colonnes requises de cette table sont manquantes
                has_blocking_issues = True
                for col in required_cols:
                    results["errors"].append({
                        "table": table_name,
                        "message": f"Table absente — colonne requise **{col}** introuvable.",
                    })
                continue

            # ── Colonnes réelles ─────────────────────────────────────────────
            conn = sqlite3.connect(abs_gpkg_path)
            cursor = conn.cursor()
            cursor.execute(f'PRAGMA table_info("{table_name}")')
            actual_columns = [info[1] for info in cursor.fetchall()]
            conn.close()

            missing_cols = [c for c in target_columns if c not in actual_columns]
            missing_required = [c for c in missing_cols if c in required_cols]
            missing_optional = [c for c in missing_cols if c not in required_cols]

            # Colonnes requises absentes → bloquant
            if missing_required:
                has_blocking_issues = True
                for col in missing_required:
                    results["errors"].append({
                        "table": table_name,
                        "message": f"Colonne requise **{col}** manquante dans la table.",
                    })

            # Colonnes optionnelles absentes → niveau 3
            if missing_optional:
                has_missing_optional = True
                for col in missing_optional:
                    results["warnings"].append({
                        "table": table_name,
                        "message": f"Colonne optionnelle **{col}** absente (données partielles).",
                    })
                results["columns_to_skip"].setdefault(table_name, []).extend(missing_optional)

            # ── Validation du contenu (types / contraintes) ──────────────────
            available_cols = [c for c in target_columns if c in actual_columns]
            if not available_cols:
                continue

            cols_query = ", ".join([f'"{c}"' for c in available_cols])
            conn = sqlite3.connect(abs_gpkg_path)
            
            # CORRECTION : On garde le type natif (int, float, None) au lieu de tout forcer en str,
            # sinon frictionless valide des strings et ne lève jamais d'erreur pour le type 'int'.
            conn.row_factory = lambda cur, row: dict(
                zip(
                    [col[0] for col in cur.description],
                    [str(val) if isinstance(val, int) and val in [0, 1] else val for val in row],
                )
            )
            cursor = conn.cursor()
            cursor.execute(f'SELECT {cols_query} FROM "{table_name}"')
            rows = cursor.fetchall()
            conn.close()

            resource = Resource(data=rows, schema=schema_obj)
            report = resource.validate()

            if not report.valid:
                summary = {}
                for error in report.tasks[0].errors:
                    if hasattr(error, "code") and error.code in ["label-error", "missing-label"]:
                        continue
                    field = getattr(error, "field_name", "Structure/Général")
                    val = getattr(error, "cell", "N/A")
                    err_type = (
                        error.code.replace("-", " ").capitalize()
                        if hasattr(error, "code") and error.code
                        else "Erreur de format"
                    )
                    summary.setdefault(field, {}).setdefault(
                        err_type, {"count": 0, "values": []}
                    )
                    summary[field][err_type]["count"] += 1
                    repr_val = f"'{val}'" if val is not None else "VIDE"
                    if (
                        repr_val not in summary[field][err_type]["values"]
                        and len(summary[field][err_type]["values"]) < 5
                    ):
                        summary[field][err_type]["values"].append(repr_val)

                for field, types in summary.items():
                    is_required = field in required_cols
                    for err_type, info in types.items():
                        sample = ", ".join(info["values"])
                        if is_required:
                            has_blocking_issues = True
                            msg = (
                                f"Champ requis **{field}** : {info['count']} erreur(s) "
                                f"de type '{err_type}'."
                            )
                            if info["values"]:
                                msg += f" (Exemples : {sample})"
                            results["errors"].append({"table": table_name, "message": msg})
                        else:
                            has_invalid_optional = True
                            results["columns_to_skip"].setdefault(table_name, []).append(field)
                            msg = (
                                f"Champ optionnel **{field}** : {info['count']} erreur(s) "
                                f"de type '{err_type}' — colonne exclue du chargement."
                            )
                            if info["values"]:
                                msg += f" (Exemples : {sample})"
                            results["warnings"].append({"table": table_name, "message": msg})

        except sqlite3.OperationalError as e:
            has_blocking_issues = True
            results["errors"].append({
                "table": table_name,
                "message": f"Erreur de lecture SQL : {str(e)}",
            })
        except Exception as e:
            has_blocking_issues = True
            results["errors"].append({
                "table": table_name,
                "message": str(e) or "Erreur de traitement inconnue",
            })

    # ── Détermination du niveau final ────────────────────────────────────────
    results["columns_to_skip"] = {
        t: list(dict.fromkeys(cols))
        for t, cols in results["columns_to_skip"].items()
    }

    if has_blocking_issues:
        results["valid"] = False
        results["level"] = None
        logger.warning("🚫 Validation échouée : colonnes requises absentes ou invalides.")
    elif has_missing_optional or has_invalid_optional:
        # Des colonnes optionnelles sont soit absentes, soit invalides
        results["level"] = 2
        if has_missing_optional:
            logger.info("📊 Niveau 2 — Colonnes optionnelles manquantes.")
        else:
            logger.info("📊 Niveau 2 — Colonnes optionnelles invalides exclues.")
    else:
        results["level"] = 1
        logger.info("📊 Niveau 1 — Schéma complet respecté. Pipeline intégral.")

    return results