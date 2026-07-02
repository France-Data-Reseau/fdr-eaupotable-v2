import os
import uuid
import re
import requests

import psycopg
from psycopg.rows import dict_row

from fastapi import FastAPI, UploadFile, File, Request, HTTPException, Query, Depends
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from fastapi_oidc import IDToken, get_auth

from fdr_etl.core.config import Config
from fdr_etl.worker.tasks import run_validation_task, run_integration_task
from fdr_etl.etl.load import extract_siren_from_gpkg

from celery.result import AsyncResult
from fdr_etl.worker.app import celery_app

DASHBOARD_ID = "c1910792-88c6-4ede-b65d-997cdd4652a0"

# ---------------------------------------------------------------------------
# Authentification OIDC
# ---------------------------------------------------------------------------

authenticate = get_auth(
    base_authorization_server_uri=f"{Config.KEYCLOAK_URL}/realms/{Config.KEYCLOAK_REALM}",
    issuer=f"{Config.KEYCLOAK_ISSUER}/realms/{Config.KEYCLOAK_REALM}",
    client_id=Config.KEYCLOAK_CLIENT_ID,
    signature_cache_ttl=3600,
    audience=Config.KEYCLOAK_CLIENT_ID,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def secure_filename(filename: str) -> str:
    """Restreint les caractères aux alphanumériques, points, underscores, tirets."""
    if not filename:
        return "unnamed_file"
    filename = os.path.basename(filename)
    filename = re.sub(r'[^a-zA-Z0-9_.-]', '_', filename)
    filename = filename.strip('_.-')
    return filename if filename else "unnamed_file"

def fetch_superset_guest_token(dashboard_id: str):
    """
    Logique métier pour obtenir le token via l'API de Superset.
    Utilise un compte de service local Superset, indépendant de Keycloak.
    """
    superset_url = "http://superset:8088"

    auth_payload = {
        "username": Config.SUPERSET_SERVICE_USERNAME,
        "password": Config.SUPERSET_SERVICE_PASSWORD,
        "provider": "db"
    }
    auth_res = requests.post(f"{superset_url}/api/v1/security/login", json=auth_payload)
    if auth_res.status_code != 200:
        return None

    token = auth_res.json()["access_token"]

    headers = {"Authorization": f"Bearer {token}"}
    guest_payload = {
        "user": {"username": "fdr_guest", "first_name": "Jonathan", "last_name": "Brans"},
        "resources": [{"type": "dashboard", "id": dashboard_id}],
        "rls": []
    }
    guest_res = requests.post(
        f"{superset_url}/api/v1/security/guest_token/",
        json=guest_payload,
        headers=headers
    )
    return guest_res.json().get("token")

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

    if os.path.exists(schemas_dir):
        app.mount("/schemas", StaticFiles(directory=schemas_dir), name="schemas")

    # -----------------------------------------------------------------------
    # Routes publiques (pas de token requis)
    # -----------------------------------------------------------------------

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={
            "keycloak_issuer": Config.KEYCLOAK_ISSUER,
            "keycloak_realm": Config.KEYCLOAK_REALM,
            "keycloak_client_id": Config.KEYCLOAK_CLIENT_ID,
        })

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
            "result": task_result.result if task_result.ready() else None
        }
        return JSONResponse(content=response)

    # -----------------------------------------------------------------------
    # Routes protégées (Bearer JWT requis)
    # -----------------------------------------------------------------------

    @app.get("/api/superset-guest-token")
    async def get_token(current_user: IDToken = Depends(authenticate)):
        token = fetch_superset_guest_token(DASHBOARD_ID)
        if not token:
            raise HTTPException(status_code=500, detail="Erreur Superset")
        return {
            "token": token,
            "dashboard_id": DASHBOARD_ID
        }

    @app.post("/upload")
    async def upload_file(
        file: UploadFile = File(...),
        current_user: IDToken = Depends(authenticate),
    ):
        if not file.filename:
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
                "filepath": filepath
            }
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
                {"error": "Fichier introuvable ou chemin invalide"},
                status_code=400
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
                status_code=400
            )

        if not import_id:
            return JSONResponse(
                {"error": "Le paramètre 'import_id' est requis pour isoler les statistiques."},
                status_code=400
            )

        task = run_integration_task.delay(
            filepath,
            import_id,
            collectivite_id,
            validation_report=validation_report
        )

        return JSONResponse(
            status_code=202,
            content={
                "message": "Validation confirmée, intégration lancée.",
                "task_id": task.id,
                "import_id": import_id
            }
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
                    cur.execute("""
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
                    """, {"import_id": import_id, "tolerance": tolerance})

                    result = cur.fetchone()

            if not result or not result.get("geojson"):
                return JSONResponse(
                    {"type": "FeatureCollection", "features": []},
                    status_code=200
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
                    cur.execute("""
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
                    """, (import_id,))
                    indicators = cur.fetchone()

                    cur.execute("""
                        SELECT annee, categorie_materiau, besoin_renouvellement_km, cout_renouvellement_euro
                        FROM besoin_renouvellement
                        WHERE file_id = %s AND scope = 'INDIVIDUAL'
                        ORDER BY annee, categorie_materiau
                    """, (import_id,))
                    projections = cur.fetchall()

                    return {
                        "indicators": indicators,
                        "projections": projections,
                        "import_id": import_id
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

                    return {
                        "indicators": indicators,
                        "projections": projections
                    }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Erreur base de données : {e}")

    return app


app = create_app()