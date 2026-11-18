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

    # Sessions and CSRF are signed/derived with this key; refuse to start without it instead of serving unauthenticated.
    # Nothing below (resolve_session hook, auth blueprint) is registered when this fails.
    if not (app.config.get("SECRET_KEY") or "").strip():
        raise RuntimeError("SECRET_KEY가 비어 있어요. .env에 SECRET_KEY를 설정한 뒤 다시 시작해 주세요.")

    from app.routes import api

    from app.books.routes import books_api
    from app.requests.routes import requests_api
    from app.auth.routes import api as auth_api, resolve_session

    from app.admin.routes import admin_api

    from app.books.sources import configure_sources
    from app.cors import init_cors

    db.init_app(app)
    init_cors(app)
    configure_sources(app)
    app.before_request(resolve_session)
    app.register_blueprint(auth_api)
    app.register_blueprint(api)
    app.register_blueprint(books_api)
    app.register_blueprint(requests_api)
    app.register_blueprint(admin_api)
    return app
