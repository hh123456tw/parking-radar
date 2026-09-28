"""回歸測試：Service Worker 由根路徑提供、不可快取，並帶入與首頁相同的內容版本。"""

from app import compute_asset_version, create_app


def test_service_worker_is_served_from_root_without_cache():
    """根路徑腳本天生可控制整個站台；no-cache 讓瀏覽器每次都檢查新版。"""
    client = create_app({"TESTING": True}).test_client()

    response = client.get("/sw.js")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/javascript")
    assert response.headers["Cache-Control"] == "no-cache"
    body = response.get_data(as_text=True)
    assert "{{" not in body
    assert f'ASSET_VERSION = "{compute_asset_version()}"' in body


def test_index_and_service_worker_share_asset_version():
    client = create_app({"TESTING": True}).test_client()

    page = client.get("/").get_data(as_text=True)

    assert f"app.js?v={compute_asset_version()}" in page
    assert f'data-asset-version="{compute_asset_version()}"' in page


def test_asset_version_changes_when_any_asset_content_changes(tmp_path):
    (tmp_path / "a.js").write_text("one", encoding="utf-8")
    before = compute_asset_version(tmp_path, ("a.js",))
    (tmp_path / "a.js").write_text("two", encoding="utf-8")

    assert compute_asset_version(tmp_path, ("a.js",)) != before
