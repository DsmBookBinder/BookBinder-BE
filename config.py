import os

from sqlalchemy import URL


class Config:
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    def __init__(self):
        self.SECRET_KEY = os.getenv("SECRET_KEY")
        self.SQLALCHEMY_DATABASE_URI = URL.create(
            "mysql+pymysql",
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "3306")),
            username=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME", "bookbinder"),
            query={"charset": "utf8mb4"},
        )
