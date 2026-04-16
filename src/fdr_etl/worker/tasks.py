import logging

from fdr_etl.core.config import Config
from fdr_etl.etl.load import load_file_to_db
from fdr_etl.etl.transform import run_transformations
from fdr_etl.etl.validate import validate_file

from .app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True)
def run_etl_pipeline(self, filepath: str):
    """
    Tâche asynchrone orchestrant le pipeline ETL complet.
    1. Validation
    2. Load (base postgres)
    3. Transformation (postgis)
    4. Notification
    """
    logger.info(f"Démarrage du pipeline ETL pour: {filepath}")

    # 1. Validation
    is_valid = validate_file(filepath)
    if not is_valid:
        logger.error("Echec de la validation.")
        return {"status": "error", "message": "Validation failed"}

    # 2. Load
    db_url = Config.DATABASE_URL
    if not db_url:
        logger.error("DATABASE_URL manquante.")
        return {"status": "error", "message": "Database configuration error"}

    try:
        load_file_to_db(filepath, db_url)
        logger.info("Load successful.")
    except Exception as e:
        logger.exception(f"Echec du load: {e}")
        return {"status": "error", "message": "Load failed"}

    # 3. Transform
    logger.info("Lancement des transformations.")
    try:
        run_transformations(db_url)
        logger.info("Transform successful.")
    except Exception as e:
        logger.exception(f"Echec du transform: {e}")
        return {"status": "error", "message": "Transform failed"}

    # 4. Notify
    # notify_user()

    logger.info("Pipeline terminé avec succès.")
    return {"status": "success", "message": "ETL completed successfully"}
