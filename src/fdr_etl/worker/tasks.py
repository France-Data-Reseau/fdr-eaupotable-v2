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
    Cette tâche vérifie uniquement la conformité du fichier .gpkg.
    Renvoie le rapport et le filepath pour l'étape suivante.
    """
    logger.info(f"Démarrage de la validation pour : {filepath}")

    # 1. Validation
    validation_res = validate_file(filepath)
    
    if not validation_res["valid"]:
        return {
            "status": "error", 
            "message": "Le schéma n'est pas respecté.",
            "errors": validation_res["errors"]
        }

    logger.info("Validation réussie.")
    return {
        "status": "success", 
        "message": "Fichier conforme, prêt pour l'intégration.",
        "filepath": filepath,
        "validation_report": validation_res.get("errors", []) # Liste vide si 0 erreur
    }


@celery_app.task(bind=True)
def run_integration_task(self, filepath: str, import_id: str, collectivite_id: str):
    """
    Étape 2 : Chargement et Transformations métier.
    S'exécute après confirmation de l'utilisateur dans l'interface.
    """
    db_url = Config.DATABASE_URL
    if not db_url:
        logger.error("DATABASE_URL manquante.")
        return {"status": "error", "message": "Erreur de configuration de la base de données."}

    # 2. Load (base postgres)
    logger.info(f"Chargement en base pour : {filepath} (ID: {import_id})")
    try:
        load_file_to_db(filepath, db_url, import_id, collectivite_id)
        logger.info("Load successful.")
    except Exception as e:
        logger.exception(f"Échec du chargement : {e}")
        return {"status": "error", "message": f"Le chargement a échoué : {str(e)}"}

    # 3. Transformation (postgis)
    logger.info("Lancement des transformations métier.")
    try:
        run_transformations(db_url,import_id)
        logger.info("Transform successful.")
    except Exception as e:
        logger.exception(f"Échec des transformations : {e}")
        return {"status": "error", "message": f"Les transformations ont échoué : {str(e)}"}

    logger.info("Pipeline d'intégration terminé avec succès.")
    return {
        "status": "success", 
        "message": "Données intégrées et transformées avec succès."
    }