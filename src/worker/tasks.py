from .app import celery_app
from etl.validate import validate_file
from etl.load import load_file_to_db
from etl.transform import run_transformations

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
    # db_url = Config.DATABASE_URL
    # load_file_to_db(filepath, db_url)
    print("[Worker] Load mock execution.")
    
    # 3. Transform
    # run_transformations(db_url)
    print("[Worker] Transform mock execution.")
    
    # 4. Notify
    # notify_user()
    
    print("[Worker] Pipeline terminé avec succès.")
    return {"status": "success", "message": "ETL completed successfully"}
