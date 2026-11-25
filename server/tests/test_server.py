import time

import pytest
from fastapi.testclient import TestClient

from roomcam_server.config import Settings
from roomcam_server.main import create_app


@pytest.fixture
def app(tmp_path):
    s = Settings()
    s.database_path = str(tmp_path / "test.db")
    s.jwt_secret = "test-secret-" + "x" * 40
    s.min_push_interval_seconds = 0
    s.phone_app_dir = str(tmp_path / "no-phone")
    return create_app(s)


@pytest.fixture
def client(app):
    return TestClient(app)


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def make_laptop(client, email="fabian@example.com"):
    client.post("/api/auth/register", json={"email": email, "password": "contraseña-larga"})
    r = client.post("/api/auth/login", json={"email": email, "password": "contraseña-larga", "device_name": "HP"})
    assert r.status_code == 200, r.text
    return r.json()


def pair_phone(client, laptop):
    code = client.post("/api/pairing/codes", headers=auth(laptop)).json()["code"]
    r = client.post("/api/pairing/claim", json={"code": code, "device_name": "iPhone"})
    assert r.status_code == 200, r.text
    return r.json(), code


def test_login_wrong_password_and_rate_limit(client):
    make_laptop(client)
    for _ in range(5):
        r = client.post("/api/auth/login", json={"email": "fabian@example.com", "password": "mal"})
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"email": "fabian@example.com", "password": "contraseña-larga"})
    assert r.status_code == 429


def test_pairing_code_is_single_use(client):
    laptop = make_laptop(client)
    phone, code = pair_phone(client, laptop)
    assert phone["kind"] == "phone"
    r = client.post("/api/pairing/claim", json={"code": code})
    assert r.status_code == 400 and "ya se usó" in r.json()["detail"]


def test_pairing_code_expires(app, client):
    laptop = make_laptop(client)
    code = client.post("/api/pairing/codes", headers=auth(laptop)).json()["code"]
    app.state.db.execute("UPDATE pairing_codes SET expires_at = ?", (time.time() - 1,))
    r = client.post("/api/pairing/claim", json={"code": code})
    assert r.status_code == 400 and "caducó" in r.json()["detail"]


def test_phone_cannot_create_codes_or_send_motion(client):
    laptop = make_laptop(client)
    phone, _ = pair_phone(client, laptop)
    assert client.post("/api/pairing/codes", headers=auth(phone)).status_code == 403
    assert client.post("/api/events/motion", json={}, headers=auth(phone)).status_code == 403


def test_revoke_invalidates_access_and_refresh(client):
    laptop = make_laptop(client)
    phone, _ = pair_phone(client, laptop)
    assert client.get("/api/devices", headers=auth(phone)).status_code == 200
    assert client.delete(f"/api/devices/{phone['device_id']}", headers=auth(laptop)).status_code == 200
    assert client.get("/api/devices", headers=auth(phone)).status_code == 401
    assert client.post("/api/auth/refresh", json={"refresh_token": phone["refresh_token"]}).status_code == 401


def test_refresh_rotates(client):
    laptop = make_laptop(client)
    r = client.post("/api/auth/refresh", json={"refresh_token": laptop["refresh_token"]})
    assert r.status_code == 200
    again = client.post("/api/auth/refresh", json={"refresh_token": laptop["refresh_token"]})
    assert again.status_code == 401


def test_other_account_cannot_revoke(client):
    a = make_laptop(client, "a@example.com")
    b = make_laptop(client, "b@example.com")
    assert client.delete(f"/api/devices/{a['device_id']}", headers=auth(b)).status_code == 404


def test_motion_event_stored_and_listed(client):
    laptop = make_laptop(client)
    phone, _ = pair_phone(client, laptop)
    r = client.post("/api/events/motion", json={}, headers=auth(laptop))
    assert r.json()["stored"] is True and r.json()["push_enabled"] is False
    events = client.get("/api/events", headers=auth(phone)).json()["events"]
    assert len(events) == 1 and events[0]["laptop_name"] == "HP"
    client.delete("/api/events", headers=auth(phone))
    assert client.get("/api/events", headers=auth(phone)).json()["events"] == []


def test_motion_sends_minimal_push(app, client):
    sent = []
    app.state.settings.vapid_public_key = "pub"
    app.state.settings.vapid_private_key = "priv"
    app.state.pusher.send = lambda sub, payload: sent.append(payload) or "sent"
    laptop = make_laptop(client)
    phone, _ = pair_phone(client, laptop)
    client.put("/api/devices/me/push", json={"endpoint": "https://push.example/x", "keys": {"p256dh": "a", "auth": "b"}}, headers=auth(phone))
    r = client.post("/api/events/motion", json={}, headers=auth(laptop))
    assert r.json()["notified"] == 1
    assert set(sent[0]) == {"type", "ts", "laptop_id", "laptop_name", "url"}
    # Alertas desactivadas: no se envía nada.
    client.patch("/api/devices/me", json={"alerts_enabled": False}, headers=auth(phone))
    r = client.post("/api/events/motion", json={}, headers=auth(laptop))
    assert r.json()["notified"] == 0


def test_turn_credentials(app, client):
    laptop = make_laptop(client)
    assert client.get("/api/ice", headers=auth(laptop)).json()["turn"] is False
    app.state.settings.turn_urls = "turn:turn.example.com:3478"
    app.state.settings.turn_secret = "s3cret"
    ice = client.get("/api/ice", headers=auth(laptop)).json()
    assert ice["turn"] is True and ice["ice_servers"][-1]["username"].endswith(laptop["device_id"])
    app.state.settings.stun_urls = ""
    assert all(s["urls"] for s in client.get("/api/ice", headers=auth(laptop)).json()["ice_servers"])


def test_signaling_relay_and_presence(client):
    laptop = make_laptop(client)
    phone, _ = pair_phone(client, laptop)
    with client.websocket_connect("/ws") as lws:
        lws.send_json({"type": "auth", "token": laptop["access_token"]})
        assert lws.receive_json()["type"] == "auth_ok"
        lws.send_json({"type": "status", "monitoring": True, "camera": "Cámara HP"})
        with client.websocket_connect("/ws") as pws:
            pws.send_json({"type": "auth", "token": phone["access_token"]})
            assert pws.receive_json()["type"] == "auth_ok"
            pres = pws.receive_json()
            assert pres["type"] == "presence" and pres["online"] and pres["monitoring"]
            pws.send_json({"type": "offer", "to": laptop["device_id"], "sdp": "v=0", "secret": "ignorado"})
            got = lws.receive_json()
            assert got == {"type": "offer", "sdp": "v=0", "from": phone["device_id"]}


def test_signaling_rejects_bad_token_and_cross_account(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": "falso"})
        assert ws.receive_json()["type"] == "auth_error"
    a = make_laptop(client, "a@example.com")
    b = make_laptop(client, "b@example.com")
    with client.websocket_connect("/ws") as bws:
        bws.send_json({"type": "auth", "token": a["access_token"]})
        bws.receive_json()
        with client.websocket_connect("/ws") as other:
            other.send_json({"type": "auth", "token": b["access_token"]})
            other.receive_json()
            other.send_json({"type": "offer", "to": a["device_id"], "sdp": "x"})
            assert other.receive_json()["type"] == "error"


def test_delete_account(client):
    laptop = make_laptop(client)
    phone, _ = pair_phone(client, laptop)
    assert client.request("DELETE", "/api/account", json={"password": "mal"}, headers=auth(laptop)).status_code == 401
    assert client.request("DELETE", "/api/account", json={"password": "contraseña-larga"}, headers=auth(laptop)).status_code == 200
    assert client.get("/api/devices", headers=auth(phone)).status_code == 401
    r = client.post("/api/auth/login", json={"email": "fabian@example.com", "password": "contraseña-larga"})
    assert r.status_code == 401
