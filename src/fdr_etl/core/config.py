import os

from dotenv import load_dotenv

# Charge les variables locales pour le dev si le fichier .env existe
load_dotenv()


class Config:
    DATABASE_URL = os.getenv("DATABASE_URL")
    REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
    CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key")
    # Keycloak OIDC
    KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
    KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER", "http://localhost:8080")
    KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "fdr")
    KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "fdr-etl")
    # Superset
    SUPERSET_INTERNAL_URL = os.getenv("SUPERSET_INTERNAL_URL", "http://superset:8088")
    SUPERSET_SERVICE_USERNAME = os.getenv("SUPERSET_SERVICE_USERNAME", "svc_fdr_api")
    SUPERSET_SERVICE_PASSWORD = os.getenv("SUPERSET_SERVICE_PASSWORD", "")
