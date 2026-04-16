import os
import uuid
import re

from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from fdr_etl.core.config import Config
from fdr_etl.worker.tasks import run_etl_pipeline


def secure_filename(filename: str) -> str:
    """
    Restricts characters to alphanumeric, dot, underscore, dash.
    """
    if not filename:
        return "unnamed_file"
    filename = os.path.basename(filename)
    filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
    filename = filename.strip('_.-')
    return filename if filename else "unnamed_file"


def create_app() -> FastAPI:
    app = FastAPI(title="FDR ETL API")

    # Répertoire de stockage temporaire (doit exister)
    base_dir = os.path.dirname(__file__)
    templates = Jinja2Templates(directory=os.path.join(base_dir, "templates"))

    UPLOAD_FOLDER = "/tmp/uploads"
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html")

    @app.post("/upload")
    async def upload_file(file: UploadFile = File(...)):
        if not file.filename:
            return JSONResponse({"error": "No selected file"}, status_code=400)

        # Sauvegarde locale du fichier
        unique_filename = f"{uuid.uuid4()}_{secure_filename(file.filename)}"
        filepath = os.path.join(UPLOAD_FOLDER, unique_filename)

        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)

        # Lancement de la tâche asynchrone Celery
        task = run_etl_pipeline.delay(filepath)

        return JSONResponse(
            status_code=202,
            content={
                "message": "Fichier reçu, traitement asynchrone lancé.",
                "task_id": task.id,
            }
        )

    return app

app = create_app()
