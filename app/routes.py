from flask import Blueprint

api = Blueprint("api", __name__)


@api.get("/")
def index():
    return {"message": "BookBinder 백엔드에 오신 것을 환영합니다!"}


@api.get("/api/health")
def health():
    return {"status": "ok", "service": "BookBinder"}
