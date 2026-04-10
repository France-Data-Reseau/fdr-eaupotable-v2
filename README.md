# FDR Eau Potable ETL

Ce projet est un pipeline ETL léger conçu pour le traitement de données dans le cadre du cas d'usage Eau Potable porté par France Data Réseau. 


## 🚀 Fonctionnalités

- **Interface Web de dépôt** : Permet aux utilisateurs de déposer des fichiers via une interface web pour validation et intégration.
- **Validation stricte** : Utilisation de `frictionless` et `Table Schema` pour valider la structure des données (colonnes, types) avant insertion.
- **Traitement Asynchrone** : Orchestration des tâches par Celery et Redis pour ne pas bloquer l'interface utilisateur.
- **CLI** : Outil en ligne de commande pour valider des fichiers localement.
- **Dockerisé** : Environnements de développement (hot-reload) et de production (Gunicorn) prêts à l'emploi.

---

## 🛠 Architecture

- **`src/web`** : Application Flask (API et Interface).
- **`src/worker`** : Configuration Celery et définition des tâches asynchrones.
- **`src/etl`** : Logique métier pure (validation, chargement, transformation), indépendante des frameworks.
- **`src/cli`** : Point d'entrée pour l'utilisation en ligne de commande.
- **`src/core`** : Configuration centralisée et connecteurs de base de données.
- **`src/schemas`** : Définition des schémas de validation (JSON Table Schema).

---

## 📦 Installation & Démarrage

### Pré-requis
- Docker & Docker Compose
- Make (optionnel, mais recommandé)
- [uv](https://github.com/astral-sh/uv) (pour le développement local hors Docker)

### 1. Configuration initiale
Copiez le fichier d'exemple des variables d'environnement :
```bash
cp .env.example .env
```

### 2. Démarrage (Mode Développement)
Ce mode active le **hot-reload** (Flask debug) et monte votre code local dans les conteneurs.
```bash
make up
```
L'application est accessible sur : **[http://localhost:8000](http://localhost:8000)**

### 3. Démarrage (Mode Production)
Ce mode utilise **Gunicorn** comme serveur WSGI robuste.
```bash
make up ENV=prod
```

---

## 💻 Utilisation de la CLI

Pour valider un fichier en local sans passer par Docker (nécessite `uv`) :

```bash
uv sync
uv run fdr-cli validate chemin/vers/votre/fichier.gpkg
```

---

## ⚙️ Commandes Utiles (Makefile)

Le `Makefile` simplifie les interactions courantes :

| Commande | Description |
| :--- | :--- |
| `make up` | Démarre les services (Dev par défaut) |
| `make down` | Arrête et supprime les conteneurs |
| `make logs` | Affiche les logs en temps réel |
| `make build` | Reconstruit les images Docker |
| `make shell-web` | Entre dans le conteneur Web |
| `make shell-db` | Ouvre une console `psql` sur la base de données |

*Note : Ajoutez `ENV=prod` à n'importe quelle commande pour cibler l'environnement de production.*
