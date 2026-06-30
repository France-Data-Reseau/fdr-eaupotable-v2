import logging

from fdr_etl.core.config import Config
from fdr_etl.etl.load import load_file_to_db
from fdr_etl.etl.transform import run_transformations
from fdr_etl.etl.validate import validate_file
import psycopg
from .app import celery_app

logger = logging.getLogger(__name__)

@celery_app.task(bind=True)
def run_validation_task(self, filepath: str):
    """
    Étape 1 : Validation du schéma.
    Vérifie la conformité du fichier .gpkg et retourne le rapport complet
    incluant le niveau de permissivité et les colonnes à exclure.
    """
    logger.info(f"Démarrage de la validation pour : {filepath}")

    # 1. Validation
    validation_res = validate_file(filepath)
    
    # Si le niveau est None, le pipeline doit être bloqué
    if not validation_res["valid"]:
        return {
            "status": "error", 
            "message": "Le schéma n'est pas respecté (erreurs bloquantes).",
            "errors": validation_res["errors"]
        }

    logger.info(f"Validation réussie (Niveau de permissivité : {validation_res.get('level')}).")
    
    # Si le niveau est 2 ou 3, il y a des avertissements, on change le statut global en "warning"
    global_status = "success"
    if validation_res.get("level") in [2, 3]:
        global_status = "warning"

    return {
        "status": global_status,  # Permet à l'UI de voir directement "warning"
        "message": "Fichier conforme avec avertissements." if global_status == "warning" else "Fichier conforme, prêt pour l'intégration.",
        "filepath": filepath,
        "validation_report": validation_res
    }


@celery_app.task(bind=True)
def run_integration_task(self, filepath: str, import_id: str, collectivite_id: str, validation_report: dict = None):
    """
    Étape 2 : Chargement et Transformations métier.
    Reçoit le rapport de validation pour appliquer le filtrage des colonnes.
    """
    db_url = Config.DATABASE_URL
    if not db_url:
        logger.error("DATABASE_URL manquante.")
        return {"status": "error", "message": "Erreur de configuration de la base de données."}

    # Extraction sécurisée des paramètres de permissivité nichés dans le rapport de validation
    permissivity = 1
    skip_cols = {}
    
    if validation_report and "validation_report" in validation_report:
        # Cas où le dictionnaire reçu contient la clé racine 'validation_report'
        inner_report = validation_report.get("validation_report", {})
        permissivity = inner_report.get("level", 1)
        skip_cols = inner_report.get("columns_to_skip", {})
    elif validation_report:
        # Cas de secours si le dictionnaire est directement le rapport à plat
        permissivity = validation_report.get("level", 1)
        skip_cols = validation_report.get("columns_to_skip", {})

    # 2. Load (base postgres) avec filtrage dynamique
    logger.info(f"Chargement en base pour : {filepath} (ID: {import_id}) avec le niveau de permissivité: {permissivity}")
    try:
        load_file_to_db(
            filepath=filepath, 
            db_url=db_url, 
            import_id=import_id, 
            collectivite_id=collectivite_id,
            permissivity_level=permissivity,  # Transmet correctement la valeur calculée (ex: 2)
            columns_to_skip=skip_cols
        )
        logger.info("Load successful.")
    except Exception as e:
        logger.exception(f"Échec du chargement : {e}")
        return {"status": "error", "message": f"Le chargement a échoué : {str(e)}"}

    # 3. Transformation (postgis)
    logger.info("Lancement des transformations métier.")
    try:
        run_transformations(db_url, import_id)
        logger.info("Transform successful.")
    except Exception as e:
        logger.exception(f"Échec des transformations : {e}")
        return {"status": "error", "message": f"Les transformations ont échoué : {str(e)}"}

    logger.info("Pipeline d'intégration terminé avec succès.")
    return {
        "status": "success", 
        "message": "Données intégrées et transformées avec succès."
    }