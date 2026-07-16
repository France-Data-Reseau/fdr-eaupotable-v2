import logging
import os
import re
import uuid

import psycopg
from celery.result import AsyncResult
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_oidc import IDToken, get_auth
from psycopg.rows import dict_row

from fdr_etl.core.config import Config
from fdr_etl.core.logging import setup_logging
from fdr_etl.etl.load import extract_siren_from_gpkg
from fdr_etl.worker.app import celery_app
from fdr_etl.worker.tasks import run_integration_task, run_validation_task

# ---------------------------------------------------------------------------
# Authentification OIDC
# ---------------------------------------------------------------------------

setup_logging()
logger = logging.getLogger(__name__)

authenticate = get_auth(
    base_authorization_server_uri=Config.OIDC_ISSUER_URL,
    issuer=Config.OIDC_ISSUER_URL,
    client_id=Config.OIDC_CLIENT_ID,
    signature_cache_ttl=Config.OIDC_CACHE_TTL,
    audience=Config.OIDC_AUDIENCE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def secure_filename(filename: str) -> str:
    """Restreint les caractères aux alphanumériques, points, underscores, tirets."""
    if not filename:
        return "unnamed_file"
    filename = os.path.basename(filename)
    filename = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename)
    filename = filename.strip("_.-")
    return filename if filename else "unnamed_file"


def token_claim(token: IDToken, key: str, default=None):
    """Safely retrieve a claim from IDToken whether dict-like or object-like."""
    if isinstance(token, dict):
        return token.get(key, default)
    return getattr(token, key, default)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="FDR ETL API")

    base_dir = os.path.dirname(__file__)
    templates = Jinja2Templates(directory=os.path.join(base_dir, "templates"))

    UPLOAD_FOLDER = "/tmp/uploads"
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(current_dir)
    schemas_dir = os.path.join(root_dir, "schemas")
    static_dir = os.path.join(base_dir, "static")

    if os.path.exists(schemas_dir):
        app.mount("/schemas", StaticFiles(directory=schemas_dir), name="schemas")
    if os.path.exists(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # -----------------------------------------------------------------------
    # Routes publiques (pas de token requis)
    # -----------------------------------------------------------------------

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "oidc_issuer_url": Config.OIDC_ISSUER_URL,
                "oidc_client_id": Config.OIDC_CLIENT_ID,
                "oidc_base_uri": Config.OIDC_BASE_URI,
            },
        )

    @app.get("/status/{task_id}")
    async def get_status(task_id: str):
        """
        Polling Celery — appelé fréquemment par le frontend,
        ne contient pas de données sensibles.
        """
        task_result = AsyncResult(task_id, app=celery_app)
        response = {
            "task_id": task_id,
            "status": task_result.status,
            "result": task_result.result if task_result.ready() else None,
        }
        return JSONResponse(content=response)

    # -----------------------------------------------------------------------
    # Routes protégées (Bearer JWT requis)
    # -----------------------------------------------------------------------

    @app.post("/upload")
    async def upload_file(
        file: UploadFile = File(...),
        current_user: IDToken = Depends(authenticate),
    ):
        logger.debug(
            "Upload auth context user_sub=%s azp=%s aud=%s groups=%s filename=%s",
            token_claim(current_user, "sub"),
            token_claim(current_user, "azp"),
            token_claim(current_user, "aud"),
            token_claim(current_user, "groups"),
            file.filename,
        )
        if not file.filename:
            logger.warning("Upload rejected: missing filename")
            return JSONResponse({"error": "No selected file"}, status_code=400)

        import_id = str(uuid.uuid4())
        unique_filename = f"{import_id}_{secure_filename(file.filename)}"
        filepath = os.path.join(UPLOAD_FOLDER, unique_filename)

        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)

        task = run_validation_task.delay(filepath)

        return JSONResponse(
            status_code=202,
            content={
                "message": "Fichier reçu, traitement asynchrone lancé.",
                "task_id": task.id,
                "import_id": import_id,
                "filepath": filepath,
            },
        )

    @app.post("/integrate")
    async def integrate_data(
        request: Request,
        current_user: IDToken = Depends(authenticate),
    ):
        data = await request.json()
        filepath = data.get("filepath")
        import_id = data.get("import_id")
        collectivite_id = data.get("collectivite_id")
        validation_task_id = data.get("validation_task_id")

        validation_report = None
        if validation_task_id:
            val_result = AsyncResult(validation_task_id, app=celery_app)
            if val_result.ready():
                task_output = val_result.result
                if isinstance(task_output, dict):
                    validation_report = task_output.get("validation_report")

        if not filepath or not os.path.exists(filepath):
            return JSONResponse(
                {"error": "Fichier introuvable ou chemin invalide"}, status_code=400
            )

        if not collectivite_id:
            collectivite_id = extract_siren_from_gpkg(filepath)

        if not collectivite_id:
            return JSONResponse(
                {
                    "error": (
                        "Impossible de déterminer la collectivité : le paramètre 'collectivite_id' "
                        "est absent et aucun numéro SIREN n'a été détecté dans la couche 'aep_perimetre'."
                    )
                },
                status_code=400,
            )

        if not import_id:
            return JSONResponse(
                {
                    "error": "Le paramètre 'import_id' est requis pour isoler les statistiques."
                },
                status_code=400,
            )

        task = run_integration_task.delay(
            filepath, import_id, collectivite_id, validation_report=validation_report
        )

        return JSONResponse(
            status_code=202,
            content={
                "message": "Validation confirmée, intégration lancée.",
                "task_id": task.id,
                "import_id": import_id,
            },
        )

    @app.get("/api/get-network/{import_id}")
    async def get_network(
        import_id: str,
        zoom: int = Query(default=12, ge=0, le=20),
        current_user: IDToken = Depends(authenticate),
    ):
        tolerance = max(0.0, (16 - zoom) * 6.0)

        try:
            with psycopg.connect(Config.DATABASE_URL, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT json_build_object(
                            'type', 'FeatureCollection',
                            'features', COALESCE(
                                json_agg(
                                    json_build_object(
                                        'type', 'Feature',
                                        'geometry', ST_AsGeoJSON(
                                            ST_Transform(
                                                ST_Simplify(geom, %(tolerance)s),
                                                4326
                                            )
                                        )::json,
                                        'properties', json_build_object()
                                    )
                                ),
                                '[]'::json
                            )
                        ) AS geojson
                        FROM aep_canalisation
                        WHERE file_id = %(import_id)s
                          AND geom IS NOT NULL;
                    """,
                        {"import_id": import_id, "tolerance": tolerance},
                    )

                    result = cur.fetchone()

            if not result or not result.get("geojson"):
                return JSONResponse(
                    {"type": "FeatureCollection", "features": []}, status_code=200
                )

            return result["geojson"]

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur base de données : {e}")

    @app.get("/api/renewal/{import_id}")
    async def get_renewal_indicators(
        import_id: str,
        current_user: IDToken = Depends(authenticate),
    ):
        try:
            with psycopg.connect(Config.DATABASE_URL, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            backlog_total_km,
                            backlog_total_euro,
                            taux_renouvellement_annuel_pct,
                            cout_moyen_km_euro,
                            horizon_ans,
                            date_calcul
                        FROM indicateurs_renouvellement
                        WHERE file_id = %s AND scope = 'INDIVIDUAL'
                        ORDER BY date_calcul DESC
                        LIMIT 1
                    """,
                        (import_id,),
                    )
                    indicators = cur.fetchone()

                    cur.execute(
                        """
                        SELECT annee, categorie_materiau, besoin_renouvellement_km, cout_renouvellement_euro
                        FROM besoin_renouvellement
                        WHERE file_id = %s AND scope = 'INDIVIDUAL'
                        ORDER BY annee, categorie_materiau
                    """,
                        (import_id,),
                    )
                    projections = cur.fetchall()

                    return {
                        "indicators": indicators,
                        "projections": projections,
                        "import_id": import_id,
                    }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur base de données : {e}")

    @app.get("/api/renewal/global")
    async def get_global_renewal_indicators(
        current_user: IDToken = Depends(authenticate),
    ):
        try:
            with psycopg.connect(Config.DATABASE_URL, row_factory=dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT
                            backlog_total_km,
                            backlog_total_euro,
                            taux_renouvellement_annuel_pct,
                            cout_moyen_km_euro,
                            horizon_ans,
                            date_calcul
                        FROM indicateurs_renouvellement
                        WHERE scope = 'GLOBAL'
                        ORDER BY date_calcul DESC
                        LIMIT 1
                    """)
                    indicators = cur.fetchone()

                    cur.execute("""
                        SELECT annee, categorie_materiau, besoin_renouvellement_km, cout_renouvellement_euro
                        FROM besoin_renouvellement
                        WHERE scope = 'GLOBAL'
                        ORDER BY annee, categorie_materiau
                    """)
                    projections = cur.fetchall()

                    return {"indicators": indicators, "projections": projections}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur base de données : {e}")

    return app


app = create_app()
