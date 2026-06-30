import argparse
import logging
import sys

from fdr_etl.core.logging import setup_logging
from fdr_etl.etl.validate import validate_file

setup_logging()
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="FDR ETL CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate", help="Valider un fichier en local"
    )
    validate_parser.add_argument(
        "filepath", type=str, help="Chemin du fichier à valider"
    )

    args = parser.parse_args()

    if args.command == "validate":
        resultat = validate_file(args.filepath)
        
        # Test sur la clé spécifique "valid"
        if resultat["valid"]:
            logger.info("=> Succès: Le fichier est valide.")
            sys.exit(0)
        else:
            logger.error("=> Erreur: Le fichier est invalide.")
            # Affichage des erreurs
            for err in resultat["errors"]:
                logger.error(f"[{err['table']}] {err['message']}")
            sys.exit(1)


if __name__ == "__main__":
    main()
