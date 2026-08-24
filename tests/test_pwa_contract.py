"""PWA 契約測試：安裝清單、圖示、服務器殼層快取與安裝引導保持綁定。"""

import json
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture()
def manifest():
    return json.loads((ROOT / "static" / "manifest.webmanifest").read_text(encoding="utf-8"))


def test_manifest_identity_and_colors(manifest):
    assert manifest["name"] == "停車地獄雷達"
    assert manifest["short_name"]
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["background_color"] == "#0b1118"
    assert manifest["theme_color"] == "#0b1118"


def test_manifest_has_192_and_512_icons(manifest):
    icons = manifest["icons"]
    assert any(icon["sizes"] == "192x192" and icon["type"] == "image/png" for icon in icons)
    assert any(icon["sizes"] == "512x512" and icon["type"] == "image/png" for icon in icons)


def test_manifest_has_a_maskable_icon(manifest):
    maskable = [icon for icon in manifest["icons"] if icon.get("purpose") == "maskable"]
    assert len(maskable) == 1
    assert maskable[0]["sizes"] == "512x512"
    assert maskable[0]["type"] == "image/png"


@pytest.mark.parametrize("name,size", [
    ("icon-192.png", 192),
    ("icon-512.png", 512),
    ("icon-maskable-512.png", 512),
])
def test_icon_files_are_valid_png_of_expected_size(name, size):
    path = ROOT / "static" / "icons" / name
    assert path.is_file()
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (size, size)


def test_service_worker_excludes_network_only_targets_before_cache():
    """/api/、OSM 圖磚與 Google 地圖必須在快取找查前直接走網路。"""
    sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    fetch_handler = sw.split('addEventListener("fetch"', 1)[1]
    guard = fetch_handler.split("event.respondWith", 1)[0]
    for token in ("/api/", "/admin/", "tile.openstreetmap.org", "google.com/maps"):
        assert token in guard


def test_service_worker_precaches_application_shell():
    sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    assert "parking-radar-shell-voice-v1" in sw
    assert '"/"' in sw
    assert "style.css?v=voice-v1" in sw
    assert "app.js?v=voice-v1" in sw
    assert "manifest.webmanifest" in sw
    assert "icon-192.png" in sw
    assert "icon-512.png" in sw
    assert "icon-maskable-512.png" in sw


def test_index_links_manifest_theme_and_apple_touch_icon():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'rel="manifest"' in template
    assert 'name="theme-color"' in template
    assert 'rel="apple-touch-icon"' in template
