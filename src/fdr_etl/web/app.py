import os
import uuid
import re
import requests

import psycopg
from psycopg.rows import dict_row

from fastapi import FastAPI, UploadFile, File, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from fdr_etl.core.config import Config
from fdr_etl.worker.tasks import run_validation_task, run_integration_task
from fdr_etl.etl.load import extract_siren_from_gpkg

from celery.result import AsyncResult
from fdr_etl.worker.app import celery_app

DASHBOARD_ID = "ed20ef0d-1fbf-4d8c-ba0a-983eca3f19dd" 

def secure_filename(filename: str) -> str:
    """
    Restreint les caractères aux alphanumériques, points, underscores, tirets.
    """
    if not filename:
        return "unnamed_file"
    filename = os.path.basename(filename)
    filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
    filename = filename.strip('_.-')
    return filename if filename else "unnamed_file"

def fetch_superset_guest_token(dashboard_id: str):
    """
    Logique métier pour obtenir le token via l'API de Superset.
    """
    superset_url = "http://superset:8088"
    
    # Login
    auth_payload = {
        "username": "admin",
        "password": "admin",
        "provider": "db"
    }
    auth_res = requests.post(f"{superset_url}/api/v1/security/login", json=auth_payload)
    if auth_res.status_code != 200:
        return None
    
    token = auth_res.json()["access_token"]
    
    # Guest Token
    headers = {"Authorization": f"Bearer {token}"}
    guest_payload = {
        "user": {"username": "fdr_guest", "first_name": "Jonathan", "last_name": "Brans"},
        "resources": [{"type": "dashboard", "id": dashboard_id}],
        "rls": []
    }
    guest_res = requests.post(f"{superset_url}/api/v1/security/guest_token/", json=guest_payload, headers=headers)
    return guest_res.json().get("token")

def create_app() -> FastAPI:
    app = FastAPI(title="FDR ETL API")

    # Répertoire de stockage temporaire
    base_dir = os.path.dirname(__file__)
    templates = Jinja2Templates(directory=os.path.join(base_dir, "templates"))

    UPLOAD_FOLDER = "/tmp/uploads"
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(current_dir)
    schemas_dir = os.path.join(root_dir, "schemas")

    if os.path.exists(schemas_dir):
        app.mount("/schemas", StaticFiles(directory=schemas_dir), name="schemas")


    @app.get("/api/superset-guest-token")
    async def get_token():
        token = fetch_superset_guest_token(DASHBOARD_ID)
        if not token:
            raise HTTPException(status_code=500, detail="Erreur Superset")
        
        return {
            "token": token, 
            "dashboard_id": DASHBOARD_ID
        }

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html")

    @app.get("/status/{task_id}")
    async def get_status(task_id: str):
        task_result = AsyncResult(task_id, app=celery_app)
        
        response = {
            "task_id": task_id,
            "status": task_result.status, # PENDING, PROGRESS, SUCCESS, FAILURE
            "result": task_result.result if task_result.ready() else None
        }
        return JSONResponse(content=response)

    @app.post("/upload")
    async def upload_file(file: UploadFile = File(...)):
        if not file.filename:
            return JSONResponse({"error": "No selected file"}, status_code=400)

        # Génération de l'ID unique de l'import dès la réception du fichier
        import_id = str(uuid.uuid4())

        # Sauvegarde locale du fichier avec l'ID généré pour éviter toute collision
        unique_filename = f"{import_id}_{secure_filename(file.filename)}"
        filepath = os.path.join(UPLOAD_FOLDER, unique_filename)

        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)

        # Lancement de la tâche asynchrone Celery
        task = run_validation_task.delay(filepath)

        # On retourne l'import_id au client pour qu'il puisse nous le renvoyer à l'étape /integrate
        return JSONResponse(
            status_code=202,
            content={
                "message": "Fichier reçu, traitement asynchrone lancé.",
                "task_id": task.id,
                "import_id": import_id,
                "filepath": filepath
            }
        )

    @app.post("/integrate")
    async def integrate_data(request: Request):
        """
        Reçoit le filepath et l'import_id associés au fichier validé,
        puis lance l'intégration et les calculs statistiques.
        """
        data = await request.json()
        filepath = data.get("filepath")
        import_id = data.get("import_id")
        collectivite_id = data.get("collectivite_id") 

        if not filepath or not os.path.exists(filepath):
            return JSONResponse(
                {"error": "Fichier introuvable ou chemin invalide"}, 
                status_code=400
            )

        # 🔄 MODIFICATION : Si le front ne l'envoie pas, on l'extrait directement ici
        if not collectivite_id:
            collectivite_id = extract_siren_from_gpkg(filepath)

        # Si même après extraction il reste introuvable, là on lève une erreur
        if not collectivite_id:
            return JSONResponse(
                {"error": "Impossible de déterminer la collectivité : le paramètre 'collectivite_id' est absent et aucun numéro SIREN n'a été détecté dans la couche 'aep_perimetre'."}, 
                status_code=400
            )

        if not import_id:
            return JSONResponse(
                {"error": "Le paramètre 'import_id' est requis pour isoler les statistiques."}, 
                status_code=400
            )

        # Lancement de la tâche d'INTÉGRATION
        task = run_integration_task.delay(filepath, import_id, collectivite_id)

        return JSONResponse(
            status_code=202,
            content={
                "message": "Validation confirmée, intégration lancée.",
                "task_id": task.id,
                "import_id": import_id
            }
        )
    return app

app = create_app()