"""로그인 API(service/app.py)의 세션·응답 규칙을 확인한다. DB 판정은 tests/test_auth.py 가 확인하므로
여기서는 service.auth 의 두 함수를 가짜로 바꿔 DB 없이 실행한다."""
import pytest
from fastapi.testclient import TestClient

from service import app as app_module
from service import auth

USER = {"user_id": "SB260001", "user_name": "전경희", "team_cd": "DEPT_HR", "team_name": "인사팀",
        "position": "팀장", "grade": "부장", "access_level": 2, "is_admin": False}


@pytest.fixture
def state(monkeypatch):
    """가짜 DB 상태: 비밀번호 'good' 만 성공. active 를 False 로 바꾸면 로그인 뒤 계정이 비활성이 된 상황."""
    s = {"active": True, "login_result": None}

    def fake_authenticate(user_id, password, conn=None):
        if s["login_result"] is not None:
            return s["login_result"]
        if password == "good":
            return auth.LoginResult(True, auth.OK, user=dict(USER))
        return auth.LoginResult(False, auth.INVALID)

    monkeypatch.setattr(app_module.auth, "authenticate", fake_authenticate)
    monkeypatch.setattr(app_module.auth, "get_active_user", lambda uid, conn=None: dict(USER) if s["active"] else None)
    monkeypatch.setattr(app_module.chat, "answer", lambda q, k: {
        "masked_query": q, "query_tokens": [], "docs": [], "answer": "일치하는 문서를 찾지 못했습니다."})
    return s


@pytest.fixture
def client(state):
    return TestClient(app_module.app)      # with 문 없이 만들면 lifespan(모델 로딩)이 실행되지 않는다


def login(client, password="good"):
    return client.post("/api/login", json={"user_id": "SB260001", "password": password})


def test_requires_login(client):
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/chat", json={"query": "안녕"}).status_code == 401


def test_login_success_and_me(client):
    r = login(client)
    assert r.status_code == 200 and r.json()["user"]["access_level"] == 2
    assert "password" not in r.text
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "max-age" not in cookie
    me = client.get("/api/me")
    assert me.status_code == 200 and me.json() == {"user": USER}
    assert client.post("/api/chat", json={"query": "안녕"}).status_code == 200


def test_login_invalid(client):
    r = login(client, "bad")
    assert r.status_code == 401
    assert r.json() == {"reason": "invalid", "message": "사번 또는 비밀번호가 올바르지 않습니다."}
    assert client.get("/api/me").status_code == 401


def test_login_inactive(client, state):
    state["login_result"] = auth.LoginResult(False, auth.INACTIVE)
    r = login(client)
    assert r.status_code == 403 and r.json()["reason"] == "inactive" and r.json()["message"]
    assert client.get("/api/me").status_code == 401


def test_logout(client):
    login(client)
    assert client.post("/api/logout").json() == {"ok": True}
    assert client.get("/api/me").status_code == 401


def test_account_deactivated_after_login(client, state):
    login(client)
    state["active"] = False                 # 관리자가 비활성으로 바꾼 경우
    assert client.get("/api/me").status_code == 401
    state["active"] = True
    assert client.get("/api/me").status_code == 401   # 세션이 비워졌으므로 다시 로그인해야 한다


def test_tampered_cookie_rejected(client):
    client.cookies.set("dojang_session", "eyJ1c2VyX2lkIjogIlNCMjYwMDAxIn0=.abc.def")
    assert client.get("/api/me").status_code == 401


def test_input_validation(client):
    assert client.post("/api/login", json={"user_id": "", "password": "x"}).status_code == 422
    assert client.post("/api/login", json={"user_id": "SB260001", "password": ""}).status_code == 422


def test_pages_redirect_by_login_state(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    login(client)
    assert client.get("/", follow_redirects=False).status_code == 200
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_server_refuses_to_start_without_secret(monkeypatch):
    monkeypatch.setattr(app_module.config, "SESSION_SECRET", "")
    monkeypatch.setattr(app_module.pii_pipeline, "_detector", lambda: None)
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        with TestClient(app_module.app):
            pass
