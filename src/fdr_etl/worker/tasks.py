from .app import celery_app
from fdr_etl.core.config import Config
from fdr_etl.etl.validate import validate_file
from fdr_etl.etl.load import load_file_to_db
from fdr_etl.etl.transform import run_transformations


@celery_app.task(bind=True)
def run_etl_pipeline(self, filepath: str):
    """
    Tâche asynchrone orchestrant le pipeline ETL complet.
    1. Validation
    2. Load (base postgres)
    3. Transformation (postgis)
    4. Notification
    """
    print(f"[Worker] Démarrage du pipeline ETL pour: {filepath}")

    # 1. Validation
    is_valid = validate_file(filepath)
    if not is_valid:
        print("[Worker] Echec de la validation.")
        return {"status": "error", "message": "Validation failed"}

    # 2. Load
    db_url = Config.DATABASE_URL
    if not db_url:
        print("[Worker] DATABASE_URL manquante.")
        return {"status": "error", "message": "Database configuration error"}

    try:
        load_file_to_db(filepath, db_url)
        print("[Worker] Load successful.")
    except Exception as e:
        print(f"[Worker] Echec du load: {e}")
        return {"status": "error", "message": "Load failed"}
    print("[Worker] Transform mock execution.")

    # 3. Transform
    try:
        run_transformations(db_url)
        print("[Worker] Transform successful.")
    except Exception as e:
        print(f"[Worker] Echec du transform: {e}")
        return {"status": "error", "message": "Transform failed"}

    # 4. Notify
    # notify_user()

    print("[Worker] Pipeline terminé avec succès.")
    return {"status": "success", "message": "ETL completed successfully"}
