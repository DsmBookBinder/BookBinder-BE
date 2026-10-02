from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

from app.db import db
from config import Config


def create_app():
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    app = Flask(__name__)
    app.config.from_object(Config())
    app.json.ensure_ascii = False

    from app.routes import api

    db.init_app(app)
    app.register_blueprint(api)
    return app
