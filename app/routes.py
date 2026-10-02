from flask import Blueprint

api = Blueprint("api", __name__)


@api.get("/")
def index():
    return {"message": "프로젝트 초기 세팅"}


@api.get("/api/health")
def health():
    return {"status": "ok", "service": "BookBinder"}
