# superset_config.py
import logging
import os

from flask_appbuilder.security.manager import AUTH_OAUTH

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
    "EMBEDDED_SUPERSET": True,
}

ENABLE_CORS = True
CORS_OPTIONS = {
    "origins": ["http://localhost:8000"],
}

SECRET_KEY = os.getenv("SUPERSET_SECRET_KEY")
GUEST_TOKEN_JWT_SECRET = os.getenv("SUPERSET_SECRET_KEY")
GUEST_TOKEN_JWT_ALGO = "HS256"
GUEST_ROLE_NAME = "Public"

SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_HTTPONLY = True

HTTP_HEADERS = {"X-Frame-Options": "ALLOWALL"}

WTF_CSRF_ENABLED = False
TALISMAN_CONFIG = {
    "content_security_policy": {
        "frame-ancestors": ["localhost:8000", "http://localhost:8000"],
    },
    "force_https": False,
}

# ─── Authentification Keycloak OIDC ─────────────────────────────────────────

AUTH_TYPE = AUTH_OAUTH

# Keycloak

KC_INTERNAL_BASE = os.getenv("KEYCLOAK_SUPERSET_INTERNAL_URL", "http://kc-host:8080")
KC_INTERNAL = f"{KC_INTERNAL_BASE}/realms/{os.getenv('KEYCLOAK_REALM', 'fdr')}/protocol/openid-connect"
KC_PUBLIC = f"{os.getenv('KEYCLOAK_ISSUER', 'http://localhost:8080')}/realms/{os.getenv('KEYCLOAK_REALM', 'fdr')}/protocol/openid-connect"

OAUTH_PROVIDERS = [
    {
        "name": "keycloak",
        "icon": "fa-key",
        "token_key": "access_token",
        "remote_app": {
            "client_id": os.getenv("KEYCLOAK_SUPERSET_CLIENT_ID", "fdr-superset"),
            "client_secret": os.getenv("KEYCLOAK_SUPERSET_SECRET", ""),
            "api_base_url": KC_INTERNAL,
            "client_kwargs": {"scope": "openid email profile"},
            "access_token_url": f"{KC_INTERNAL}/token",
            "authorize_url": f"{KC_PUBLIC}/auth",
            "jwks_uri": f"{KC_INTERNAL}/certs",
            "server_metadata_url": f"{KC_INTERNAL_BASE}/realms/{os.getenv('KEYCLOAK_REALM', 'fdr')}/.well-known/openid-configuration",
        },
    }
]

# Création automatique des utilisateurs à la première connexion
AUTH_USER_REGISTRATION = True
AUTH_USER_REGISTRATION_ROLE = "Gamma"  # rôle par défaut — accès lecture seule

# Mapping des rôles Keycloak → rôles Superset
AUTH_ROLES_MAPPING = {
    "fdr_admin": ["Admin"],
    "fdr_user": ["Gamma"],
}

# Synchronise les rôles à chaque connexion
AUTH_ROLES_SYNC_AT_LOGIN = True

# Manager de sécurité personnalisé pour lire les rôles Keycloak
from superset.security import SupersetSecurityManager


class FDRSecurityManager(SupersetSecurityManager):
    def oauth_user_info(self, provider, response=None):
        """Extrait les infos utilisateur et rôles depuis le token Keycloak."""
        if provider == "keycloak":
            me = self.appbuilder.sm.oauth_remotes[provider].userinfo(token=response)

            roles = me.get("realm_access", {}).get("roles", [])

            return {
                "username": me.get("preferred_username"),
                "email": me.get("email"),
                "first_name": me.get("given_name", ""),
                "last_name": me.get("family_name", ""),
                "role_keys": roles,
            }
        return {}


CUSTOM_SECURITY_MANAGER = FDRSecurityManager
