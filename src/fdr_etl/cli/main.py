import argparse
import sys

from fdr_etl.etl.validate import validate_file


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
        is_valid = validate_file(args.filepath)
        if is_valid:
            print("=> Succès: Le fichier est valide.")
            sys.exit(0)
        else:
            print("=> Erreur: Le fichier est invalide.")
            sys.exit(1)


if __name__ == "__main__":
    main()
