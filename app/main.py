
from flask import Flask, jsonify


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    return app


if __name__ == "__main__":
    import os
    create_app().run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
