
import os

from flask import Flask, g, jsonify

from .db import close_db
from .errors import ApiError
from .routes import api
from scripts.migrate import migrate


def create_app(auto_migrate: bool = True) -> Flask:
    app = Flask(__name__)
    app.config["DATABASE_PATH"] = os.getenv("DATABASE_PATH", "data/app.sqlite3")
    if auto_migrate:
        from pathlib import Path
        migrate(Path(app.config["DATABASE_PATH"]))

    app.register_blueprint(api)
    app.teardown_appcontext(close_db)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError):
        db = g.get("db")
        if db is not None:
            db.rollback()
        return error.to_response()

    @app.after_request
    def commit(response):
        db = g.get("db")
        if db is not None and response.status_code < 500:
            db.commit()
        return response

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
