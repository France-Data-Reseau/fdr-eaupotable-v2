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
    # OIDC
    OIDC_ISSUER_URL = os.getenv("OIDC_ISSUER_URL")
    OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "fdr-etl")
    OIDC_BASE_URI = os.getenv("OIDC_BASE_URI", "")
    OIDC_AUDIENCE = os.getenv("OIDC_AUDIENCE", "fnccr")
    OIDC_CACHE_TTL = int(os.getenv("OIDC_CACHE_TTL", "3600"))
