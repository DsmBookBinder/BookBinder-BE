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

    from app.books.routes import books_api
    from app.requests.routes import requests_api
    from app.auth.routes import api as auth_api, resolve_session

    from app.admin.routes import admin_api

    db.init_app(app)
    app.before_request(resolve_session)
    app.register_blueprint(auth_api)
    app.register_blueprint(api)
    app.register_blueprint(books_api)
    app.register_blueprint(requests_api)
    app.register_blueprint(admin_api)
    return app
