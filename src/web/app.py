import os
import uuid

from flask import Flask, jsonify, request
from core.config import Config
from worker.tasks import run_etl_pipeline

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Répertoire de stockage temporaire (doit exister)
    UPLOAD_FOLDER = "/tmp/uploads"
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    @app.route("/", methods=["GET"])
    def index():
        return jsonify({"message": "FDR ETL API is running"}), 200

    @app.route("/upload", methods=["POST"])
    def upload_file():
        if "file" not in request.files:
            return jsonify({"error": "No file part in request"}), 400
        
        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No selected file"}), 400
        
        # Sauvegarde locale du fichier
        unique_filename = f"{uuid.uuid4()}_{file.filename}"
        filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
        file.save(filepath)
        
        # Lancement de la tâche asynchrone Celery
        task = run_etl_pipeline.delay(filepath)
        
        return jsonify({
            "message": "Fichier reçu, traitement asynchrone lancé.", 
            "task_id": task.id
        }), 202

    return app
