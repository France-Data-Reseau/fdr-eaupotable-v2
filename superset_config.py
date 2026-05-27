# superset_config.py
import logging

# Configuration du niveau de niveau de log global de Superset pour ignorer les warnings
LOG_LEVEL = "ERROR"
CONSOLE_LOG_LEVEL = "ERROR"

# Désactivation spécifique du logger root et Flask-AppBuilder
logging.getLogger("root").setLevel(logging.ERROR)
logging.getLogger("flask_appbuilder").setLevel(logging.ERROR)

FEATURE_FLAGS = {
    "ENABLE_JAVASCRIPT_CONTROLS": True,
    "ALERT_REPORTS": True,
    "VIZ_PLUGINS": True, 
    "EMBEDDED_DASHBOARD": True,
    "EMBEDDED_SUPERSET": True
}

ENABLE_CORS = True
CORS_OPTIONS = {
    'origins': ['http://localhost:8000'],
}

SECRET_KEY = 'une_cle_tres_secrete_123'
GUEST_TOKEN_JWT_SECRET = 'une_cle_tres_secrete_123'
GUEST_TOKEN_JWT_ALGO = 'HS256'
GUEST_ROLE_NAME = "Public"

SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_HTTPONLY = True

# Autoriser l'intégration dans une iframe
HTTP_HEADERS = {'X-Frame-Options': 'ALLOWALL'}

# Désactiver la protection CSRF pour l'API (peut être nécessaire pour l'embedded)
WTF_CSRF_ENABLED = False
TALISMAN_CONFIG = {
    "content_security_policy": {
        "frame-ancestors": ["localhost:8000", "http://localhost:8000"],
    },
    "force_https": False,
}