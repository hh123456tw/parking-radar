"""測試環境設定：app.py 匯入時會建立 app，缺少金鑰會拒絕啟動，因此先提供測試專用值。"""

import os

# 必須在任何測試匯入 config／app 之前設定；本機 .env 的真實值不受影響（setdefault）。
os.environ.setdefault("FLASK_SECRET_KEY", "test-only-secret-key")
