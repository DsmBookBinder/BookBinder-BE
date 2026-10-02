import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask


def create_app():
    """Flask 애플리케이션을 생성하고 API를 등록합니다."""
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
    app.json.ensure_ascii = False

    from app.routes import api

    app.register_blueprint(api)
    return app
