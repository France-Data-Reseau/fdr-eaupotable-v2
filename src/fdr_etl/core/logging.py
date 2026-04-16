import logging
import os
import sys


def setup_logging():
    """
    Configure le logging pour l'ensemble du projet.
    Par défaut, logge sur stdout au niveau INFO.
    Le niveau peut être configuré via la variable d'environnement LOG_LEVEL.
    """
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_str, logging.INFO)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
