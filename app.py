"""Flask 入口；集中協調查詢流程，不在路由內重寫分析公式。"""

from flask import Flask, jsonify
from config import Config


def create_app(test_config=None):
    """建立 Flask 應用，允許測試覆寫設定並回傳 app。"""
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    @app.get("/health")
    def health():
        """回傳不依賴外部服務的程序健康狀態。"""
        return jsonify(status="ok")

    return app


app = create_app()
