from __future__ import annotations

import sys
import types

from fastapi.testclient import TestClient

rio_tiler = types.ModuleType("rio_tiler")
rio_tiler_errors = types.ModuleType("rio_tiler.errors")
rio_tiler_io = types.ModuleType("rio_tiler.io")


class TileOutsideBounds(Exception):
    pass


class COGReader:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("COGReader is not used in health route tests")


rio_tiler_errors.TileOutsideBounds = TileOutsideBounds
rio_tiler_io.COGReader = COGReader

sys.modules.setdefault("rio_tiler", rio_tiler)
sys.modules.setdefault("rio_tiler.errors", rio_tiler_errors)
sys.modules.setdefault("rio_tiler.io", rio_tiler_io)

from hotosm_imagery_tile.main import app


def test_root_returns_service_info():
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["service"] == "hotosm-imagery-tile"
    assert data["endpoints"]["health"] == "/health"
    assert data["endpoints"]["healthz"] == "/healthz"
    assert "stac_api" in data
    assert "asset_key" in data


def test_health_alias():
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["status"] == "ok"
    assert data["service"] == "hotosm-imagery-tile"


def test_healthz():
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["status"] == "ok"
    assert data["service"] == "hotosm-imagery-tile"
