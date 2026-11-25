"""권한·보안 API 케이스 ITP01~ITP22 (it_access_cases.jsonl) 를 FastAPI TestClient 로 실행한다.

    python -m eval.integration.security --run-dir outputs/it/<실행ID>     # 단독 실행
    run.py 가 마지막에 자동으로 부른다.

- 서버를 띄우지 않고 service.app 을 프로세스 안에서 호출한다 (실제 DB 사용, chat 은 LLM 호출).
- 비밀번호: 환경변수 IT_PW_<사번>, 없으면 시연 계정 규칙(qwer + 사번 끝 4자리, README 5-4).
- 결과: security.jsonl  {id, scenario, required, result, expected, actual}  (케이스마다 바로 저장)
  result: 통과 / 실패 / 판정 보류(정책 미정 케이스: 현재 동작만 기록) / 미검증
          실행 오류(LLM 429·타임아웃·DB 연결 등. 보안 판정에서 '미완료'로 셈) / 오류(케이스 코드 예외)
- 검사 기준: 거부 케이스는 기대 HTTP 상태 + 응답에 복원 값·원문 없음 + restore_log 0행.
  부분 공개는 요청한 이름 토큰만, 값 = vault.mask_name(정답 이름) 과 정확히 같음. 전체 복원은 요청한 토큰만, 값 = 정답 원문.
  유출 검사 원문에는 함께 적재한 기존 문서(홀드아웃)의 개인정보도 넣는다.
  질문은 정답지 steps 의 chat 질문을, 사용자는 실행 전에 정답지 users 와 코드(CODE_USERS)를 대조한다 (다르면 '오류').
  결과 기록(actual)에는 원문을 남기지 않는다 (복원된 이름 등은 [원문]으로 바꿈).
- 케이스마다 X04(응답 원문 유출: 정상 공개 응답 제외)와 X06(질문마다 query_log 1행·사용자 일치, 공개·복원 성공마다
  restore_log 행 = 공개 토큰 수)을 따로 판정해 x04·x06 에 남긴다 (엑셀 tblApiSec 의 X04·X06 열).
- --resume: 통과·실패·판정 보류 결과가 있는 케이스만 건너뛰고, 미검증·실행 오류·오류·미실행 케이스는 다시 실행
"""
import argparse
import collections
import json
import os
import pathlib
import re

from .common import (EXEC_ERROR, ROOT, Gold, LLMGuard, exec_error_info, holdout_raw, is_exec_error, jl_read, jl_write,
                     token_of)

TOKEN = re.compile(r"<[A-Z_]+_[0-9A-Za-z]+>")
USERS = {"Lv2": "SB260001", "Lv2_admin": "SB260002", "Lv1": "SB260003", "Lv1_b": "SB260004", "Lv0": "SB260009"}
# 케이스 함수가 실제로 쓰는 사용자. 실행 전에 정답지(it_access_cases.users)와 대조한다.
CODE_USERS = {"ITP01": ["Lv1"], "ITP02": ["Lv0"], "ITP03": ["Lv2"], "ITP04": ["Lv1"], "ITP05": ["Lv1"], "ITP06": ["Lv1"],
              "ITP07": ["Lv0"], "ITP08": ["Lv2"], "ITP09": ["Lv2"], "ITP10": ["Lv2"], "ITP11": ["Lv2"],
              "ITP12": ["Lv1", "Lv1_b"], "ITP13": ["Lv2", "Lv2_admin"], "ITP14": ["Lv1"], "ITP15": ["Lv2"], "ITP16": ["Lv2"],
              "ITP17": ["Lv2", "Lv1"], "ITP18": ["Lv2"], "ITP19": ["Lv1"], "ITP20": ["Lv2_admin"], "ITP21": ["Lv2"],
              "ITP22": ["Lv1"]}


def mask_name(n):
    """infra/init/05_audit.sql vault.mask_name 과 같은 규칙 (기대 부분 공개 값 계산용)."""
    n = re.sub(r"\s+", "", n or "")
    if not n:
        return None
    if len(n) == 1:
        return "*"
    if len(n) == 2:
        return n[0] + "*"
    return n[0] + "*" * (len(n) - 2) + n[-1]


def _norm(v):
    return re.sub(r"[\s\-.]", "", str(v or ""))


def pw(uid):
    return os.environ.get(f"IT_PW_{uid}") or f"qwer{uid[-4:]}"


class Ctx:
    def __init__(self, app, gold, run_dir=None):
        from fastapi.testclient import TestClient
        self.TC, self.app, self.gold = TestClient, app, gold
        g = gold
        self.raw = {e["text"] for d in g.docs.values() for e in d["entities"]
                    if (e["label"] == "person" and len(e["text"]) >= 3) or (e["label"] != "person" and len(e["text"]) >= 6)}
        ho = holdout_raw(run_dir, min_name=1, min_other=1) if run_dir is not None else {}
        self.raw |= {v for v, lab in ho.items() if (lab == "person" and len(v) >= 3) or (lab != "person" and len(v) >= 6)}
        self.truth = {}                            # 토큰 → 정답 원문 (복원·부분 공개 값 검증용, 기존 문서 포함)
        for v, lab in ho.items():
            try:
                self.truth.setdefault(token_of(lab, v), v)
            except (ValueError, KeyError):
                pass
        for d in g.docs.values():
            for e in d["entities"]:
                try:
                    self.truth.setdefault(token_of(e["label"], e["text"]), e["text"])
                except (ValueError, KeyError):
                    pass
        self.basic = "한지석 고객의 대출 신청 내용 알려줘"
        self.multi = "차은호 고객의 위임 확인서 내용 알려줘"
        self.table = "11월 연체 대상 고객 목록 알려줘"
        self.case = None
        self.events = []

    def query(self, i=0):
        """지금 케이스의 정답지 steps 에 적힌 i번째 chat 질문 (코드에 하드코딩하지 않는다)."""
        qs = [st["args"]["query"] for st in (self.case or {}).get("steps", []) if st["api"] == "chat"]
        return qs[i] if len(qs) > i else self.basic

    def check_partial(self, m, requested):
        """부분 공개 결과 검증 → (True / False / None(정답을 몰라 확인 불가), 설명)
        요청한 이름 토큰이 모두 반환되고, 요청 밖·이름 아닌 토큰이 없고, 값 = mask_name(정답 이름)."""
        req_names = [t for t in requested if t.startswith("<PERSON_")]
        extra = sorted(set(m) - set(requested))
        missing = sorted(set(req_names) - set(m))
        non_name = sorted(t for t in m if not t.startswith("<PERSON_"))
        wrong = [t for t in m if t in self.truth and m[t] != mask_name(self.truth[t])]
        wrong += [t for t in m if t not in self.truth and (m[t] in self.raw or "*" not in (m[t] or ""))]
        unknown = [t for t in m if t not in self.truth]
        msg = (f"요청 {len(req_names)}·반환 {len(m)}, 누락 {len(missing)}, 요청 밖 {len(extra)}, 이름 아닌 토큰 {len(non_name)}, "
               f"마스킹 불일치 {len(wrong)}" + (f", 정답 모름 {len(unknown)}" if unknown else ""))
        if extra or missing or non_name or wrong:
            return False, msg
        return (None if unknown else True), msg

    def check_full(self, m, requested):
        """전체 복원 결과 검증 → (True / False / None(정답을 몰라 확인 불가), 설명)
        요청한 토큰이 모두 반환되고, 요청 밖 토큰이 없고, 값 = 정답 원문."""
        extra = sorted(set(m) - set(requested))
        missing = sorted(set(requested) - set(m))
        known = [t for t in m if t in self.truth]
        wrong = [t for t in known if _norm(m[t]) != _norm(self.truth[t])]
        unknown = [t for t in m if t not in self.truth]
        msg = (f"요청 {len(requested)}·반환 {len(m)}, 누락 {len(missing)}, 요청 밖 {len(extra)}, "
               f"값 일치 {len(known) - len(wrong)}/{len(known)}" + (f", 정답 모름 {len(unknown)}" if unknown else ""))
        if extra or missing or wrong:
            return False, msg
        return (None if unknown else True), msg

    def client(self, user=None):
        """로그인한 TestClient. 요청·응답을 self.events 에 남겨 케이스마다 X04(응답 원문)·X06(감사 로그)을 따로 검사한다."""
        c = self.TC(self.app)
        uid = USERS[user] if user else None
        post, get = c.post, c.get

        def rec(method, path, kw, r):
            try:
                body = r.json() if r.content else {}
            except ValueError:
                body = {"_text": r.text[:2000]}
            self.events.append({"path": path, "json": kw.get("json") or {}, "status": r.status_code, "body": body,
                                "user_id": uid})

        def post_(path, *a, **kw):
            r = post(path, *a, **kw)
            if path != "/api/login":
                rec("POST", path, kw, r)
            return r

        def get_(path, *a, **kw):
            r = get(path, *a, **kw)
            rec("GET", path, kw, r)
            return r
        c.post, c.get = post_, get_
        if user:
            r = post("/api/login", json={"user_id": uid, "password": pw(uid)})
            assert r.status_code == 200, f"{user} 로그인 실패 {r.status_code}"
        return c

    def x04(self):
        """이 케이스의 응답 원문 유출. 정상 공개(재인증 전체 복원·부분 공개·restored 답변)는 제외 → (판정, 설명)"""
        scanned, found = 0, set()
        for e in self.events:
            if e["path"] in ("/api/restore-tokens", "/api/partial-tokens") and e["status"] == 200:
                continue
            if e["path"] == "/api/chat" and (e["body"] or {}).get("restored"):
                continue
            scanned += 1
            found |= set(self.leaks(e["body"]))
        if not scanned:
            return "미검증", "검사할 응답 없음"
        return ("실패" if found else "통과"), f"응답 {scanned}개, 원문 {len(found)}종"

    def x06(self):
        """이 케이스의 감사 로그: 질문마다 query_log 1행(사용자 일치), 공개·복원 성공마다 restore_log 행 = 공개 토큰 수"""
        from starter import db
        chats = [(e["body"].get("request_id"), e["user_id"]) for e in self.events
                 if e["path"] == "/api/chat" and isinstance(e["body"], dict) and e["body"].get("request_id")]
        opened = collections.Counter()
        for e in self.events:
            if e["path"] in ("/api/restore-tokens", "/api/partial-tokens") and e["status"] == 200:
                m = (e["body"] or {}).get("map") or {}
                if m:
                    mode = "full" if e["path"] == "/api/restore-tokens" else "partial"
                    opened[(e["json"].get("request_id"), mode, e["user_id"])] += len(m)
        if not chats and not opened:
            return "미검증", "검사할 요청 없음"
        bad = []
        with db.auth_conn() as conn, conn.cursor() as cur:
            for rid, uid in chats:
                cur.execute("SELECT user_id FROM audit.query_log WHERE request_id = %s", (rid,))
                got = [r[0] for r in cur.fetchall()]
                if len(got) != 1 or got[0] != uid:
                    bad.append(f"query_log {len(got)}행")
            for (rid, mode, uid), n in opened.items():
                cur.execute("SELECT count(*) FROM audit.restore_log WHERE request_id = %s AND mode = %s AND user_id = %s",
                            (rid, mode, uid))
                got = cur.fetchone()[0]
                if got != n:
                    bad.append(f"restore_log {mode} {got}/{n}")
        return ("실패" if bad else "통과"), (f"질문 {len(chats)}건·공개 {sum(opened.values())}개 확인"
                                           + (f", 이상: {'; '.join(bad[:3])}" if bad else ""))

    def chat(self, c, query, restore=False):
        r = c.post("/api/chat", json={"query": query, "restore": restore})
        return r, (r.json() if r.status_code == 200 else {})

    def leaks(self, payload):
        s = json.dumps(payload, ensure_ascii=False)
        return sorted(v for v in self.raw if v in s)

    @staticmethod
    def tokens(body, kind="all"):
        text = (body.get("answer") or "") + " " + " ".join(d.get("snippet") or "" for d in body.get("docs", []))
        ts = sorted(set(TOKEN.findall(text)))
        if kind == "name":
            return [t for t in ts if t.startswith("<PERSON_")]
        if kind == "other":
            return [t for t in ts if not t.startswith("<PERSON_")]
        return ts

    def restore_rows(self, request_id):
        from starter import db
        with db.auth_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT mode, count(*) FROM audit.restore_log WHERE request_id = %s GROUP BY mode", (request_id,))
            return dict(cur.fetchall())

    def last_query_log(self, user_id):
        from starter import db
        with db.auth_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT state, masked_query FROM audit.query_log WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
                        (user_id,))
            return cur.fetchone()


def _combine(base_ok, check):
    """기본 조건(HTTP·행 수·유출)과 값 검증(True/False/None)을 합친다: 실패 우선, 확인 불가면 미검증(None)."""
    if not base_ok or check is False:
        return False
    return None if check is None else True


def _denied(x, resp, expect=403):
    """거부 응답 검사: 기대 HTTP 상태이고, 응답에 복원 값(map)·원문이 없음."""
    body = resp.json() if resp.content else {}
    no_map = not (isinstance(body, dict) and body.get("map"))
    ok, msg = _no_leak(x, body)
    return resp.status_code == expect and no_map and ok, f"HTTP {resp.status_code}{'' if no_map else ', 값 반환됨'}, {msg}"


def _no_leak(ctx, *payloads):
    found = sorted({v for p in payloads for v in ctx.leaks(p)})
    return (not found), (f"원문 {len(found)}종 노출" if found else "원문 0건")


def case_ITP01(x, level="Lv1"):
    c = x.client(level)
    r, b = x.chat(c, x.query())
    ok, msg = _no_leak(x, b)
    return r.status_code == 200 and ok, f"HTTP {r.status_code}, {msg}, 토큰 {len(x.tokens(b))}개"


def case_ITP02(x):
    return case_ITP01(x, "Lv0")


def case_ITP03(x):
    return case_ITP01(x, "Lv2")


def case_ITP04(x):
    c = x.client("Lv1")
    r, b = x.chat(c, x.query())
    names = x.tokens(b, "name")
    if not names:
        return None, "답변·조각에 이름 토큰이 없어 확인할 수 없음"
    c.post("/api/verify-password", json={"password": pw(USERS["Lv1"])})
    p = c.post("/api/partial-tokens", json={"request_id": b["request_id"], "tokens": names})
    m = p.json().get("map", {}) if p.status_code == 200 else {}
    rows = x.restore_rows(b["request_id"]).get("partial", 0)
    fmt_ok, fmt = x.check_partial(m, names)
    leak_ok, leak = _no_leak(x, p.json() if p.content else {})
    return (_combine(p.status_code == 200 and bool(m) and leak_ok and rows == len(m), fmt_ok),
            f"HTTP {p.status_code}, 공개 {len(m)}개 ({fmt}), {leak}, restore_log partial {rows}행")


def case_ITP05(x):
    c = x.client("Lv1")
    r, b = x.chat(c, x.query())
    other = x.tokens(b, "other")
    if not other:
        return None, "이름이 아닌 토큰이 없어 확인할 수 없음"
    c.post("/api/verify-password", json={"password": pw(USERS["Lv1"])})
    p = c.post("/api/partial-tokens", json={"request_id": b["request_id"], "tokens": other})
    m = p.json().get("map", {}) if p.status_code == 200 else {}
    rows = sum(x.restore_rows(b["request_id"]).values())
    leak_ok, leak = _no_leak(x, p.json() if p.content else {})
    return (not m) and rows == 0 and leak_ok, f"HTTP {p.status_code}, 반환 {len(m)}개, {leak}, restore_log {rows}행"


def case_ITP06(x):
    c = x.client("Lv1")
    r, b = x.chat(c, x.query())
    c.post("/api/verify-password", json={"password": pw(USERS["Lv1"])})
    p = c.post("/api/restore-tokens", json={"request_id": b.get("request_id", "req_00000000_000000000000"),
                                            "tokens": x.tokens(b) or ["<PERSON_000000000000>"]})
    ok, msg = _denied(x, p)
    rows = sum(x.restore_rows(b.get("request_id", "")).values()) if b.get("request_id") else 0
    return ok and rows == 0, f"{msg}, restore_log {rows}행"


def case_ITP07(x):
    c = x.client("Lv0")
    r, b = x.chat(c, x.query())
    rid = b.get("request_id", "req_00000000_000000000000")
    toks = x.tokens(b) or ["<PERSON_000000000000>"]
    v = c.post("/api/verify-password", json={"password": pw(USERS["Lv0"])})
    p = c.post("/api/partial-tokens", json={"request_id": rid, "tokens": toks})
    s = c.post("/api/restore-tokens", json={"request_id": rid, "tokens": toks})
    res = [_denied(x, z) for z in (v, p, s)]
    return all(ok for ok, _ in res), " / ".join(f"{n}: {m}" for n, (_, m) in zip(("verify", "partial", "restore"), res))


def case_ITP08(x):
    c = x.client("Lv2")
    r, b = x.chat(c, x.query())
    c.post("/api/verify-password", json={"password": pw(USERS["Lv2"])})
    p = c.post("/api/partial-tokens", json={"request_id": b["request_id"], "tokens": x.tokens(b, "name") or ["<PERSON_000000000000>"]})
    rows = sum(x.restore_rows(b["request_id"]).values())
    ok, msg = _denied(x, p)
    return ok and rows == 0, f"{msg}, restore_log {rows}행"


def case_ITP09(x):
    c = x.client("Lv2")
    r, b = x.chat(c, x.query())
    toks = x.tokens(b)
    if not toks:
        return None, "토큰이 없어 확인할 수 없음"
    c.post("/api/verify-password", json={"password": pw(USERS["Lv2"])})
    p = c.post("/api/restore-tokens", json={"request_id": b["request_id"], "tokens": toks})
    m = p.json().get("map", {}) if p.status_code == 200 else {}
    rows = x.restore_rows(b["request_id"]).get("full", 0)
    val_ok, val = x.check_full(m, toks)
    return (_combine(p.status_code == 200 and bool(m) and rows == len(m), val_ok),
            f"HTTP {p.status_code}, 복원 {len(m)}/{len(toks)}개 ({val}), restore_log full {rows}행")


def case_ITP10(x):
    c = x.client("Lv2")
    c.post("/api/verify-password", json={"password": pw(USERS["Lv2"])})
    r, b = x.chat(c, x.query(), restore=True)
    rows = x.restore_rows(b.get("request_id", "")).get("full", 0) if b else 0
    return bool(b.get("restored")) and rows > 0, f"restored={b.get('restored')}, restore_log full {rows}행"


def case_ITP11(x):
    c = x.client("Lv2")
    r, b = x.chat(c, x.query())
    p = c.post("/api/restore-tokens", json={"request_id": b["request_id"], "tokens": x.tokens(b) or ["<PERSON_000000000000>"]})
    r2, b2 = x.chat(c, x.query(1), restore=True)
    ok, msg = _no_leak(x, b2)
    d_ok, d_msg = _denied(x, p)
    return d_ok and not b2.get("restored") and ok, f"restore {d_msg}, chat restored={b2.get('restored')}, {msg}"


def case_ITP12(x):
    a = x.client("Lv1")
    r, b = x.chat(a, x.query())
    other = x.client("Lv1_b")
    other.post("/api/verify-password", json={"password": pw(USERS["Lv1_b"])})
    p = other.post("/api/partial-tokens", json={"request_id": b["request_id"], "tokens": x.tokens(b, "name") or ["<PERSON_000000000000>"]})
    rows = sum(x.restore_rows(b["request_id"]).values())
    ok, msg = _denied(x, p)
    return ok and rows == 0, f"{msg}, restore_log {rows}행"


def case_ITP13(x):
    a = x.client("Lv2")
    r, b = x.chat(a, x.query())
    adm = x.client("Lv2_admin")
    adm.post("/api/verify-password", json={"password": pw(USERS["Lv2_admin"])})
    p = adm.post("/api/restore-tokens", json={"request_id": b["request_id"], "tokens": x.tokens(b) or ["<PERSON_000000000000>"]})
    m = p.json().get("map", {}) if p.status_code == 200 else {}
    return "hold", f"다른 Lv2 사용자의 요청 복원: HTTP {p.status_code}, 복원 {len(m)}개"


def _absent_name_token():
    return token_of("person", "오태린")          # 기본 질문(한지석) 답변에 나오지 않는 다른 문서 인물


def case_ITP14(x):
    c = x.client("Lv1")
    r, b = x.chat(c, x.query())
    c.post("/api/verify-password", json={"password": pw(USERS["Lv1"])})
    p = c.post("/api/partial-tokens", json={"request_id": b["request_id"], "tokens": [_absent_name_token()]})
    m = p.json().get("map", {}) if p.status_code == 200 else {}
    rows = sum(x.restore_rows(b["request_id"]).values())
    leak_ok, leak = _no_leak(x, p.json() if p.content else {})
    return (not m) and rows == 0 and leak_ok, f"HTTP {p.status_code}, 반환 {len(m)}개, {leak}, restore_log {rows}행"


def case_ITP15(x):
    c = x.client("Lv2")
    r, b = x.chat(c, x.query())
    c.post("/api/verify-password", json={"password": pw(USERS["Lv2"])})
    p = c.post("/api/restore-tokens", json={"request_id": b["request_id"], "tokens": [_absent_name_token()]})
    m = p.json().get("map", {}) if p.status_code == 200 else {}
    return "hold", f"요청에 없던 이름 토큰 복원: HTTP {p.status_code}, 복원 {len(m)}개"


def case_ITP16(x):
    c = x.client("Lv2")
    r, b = x.chat(c, x.query())
    c.post("/api/verify-password", json={"password": pw(USERS["Lv2"])})
    c.post("/api/logout")
    c.post("/api/login", json={"user_id": USERS["Lv2"], "password": pw(USERS["Lv2"])})
    p = c.post("/api/restore-tokens", json={"request_id": b["request_id"], "tokens": x.tokens(b) or ["<PERSON_000000000000>"]})
    ok, msg = _denied(x, p)
    return ok, f"재로그인 후 복원 {msg}"


def case_ITP17(x):
    a = x.client("Lv2")
    r, b = x.chat(a, x.query())
    a.post("/api/verify-password", json={"password": pw(USERS["Lv2"])})
    s = x.client("Lv1")
    toks = x.tokens(b) or ["<PERSON_000000000000>"]
    r1, m1 = _denied(x, s.post("/api/restore-tokens", json={"request_id": b["request_id"], "tokens": toks}))
    r2, m2 = _denied(x, s.post("/api/partial-tokens", json={"request_id": b["request_id"], "tokens": toks}))
    rows = sum(x.restore_rows(b["request_id"]).values())
    return r1 and r2 and rows == 0, f"다른 세션: restore {m1} / partial {m2}, restore_log {rows}행"


def case_ITP18(x):
    c = x.client("Lv2")
    r, b = x.chat(c, x.query())
    c.post("/api/verify-password", json={"password": pw(USERS["Lv2"])})
    bad = ["<PERSON_000000000000>", "<PERSON_ABCDEF123456>", "PERSON_abc", "<UNKNOWN_123456789abc>"]
    p1 = c.post("/api/restore-tokens", json={"request_id": b["request_id"], "tokens": bad})
    m = p1.json().get("map", {}) if p1.status_code == 200 else {}
    p2 = c.post("/api/restore-tokens", json={"request_id": "req_bad", "tokens": x.tokens(b) or bad})
    rows = sum(x.restore_rows(b["request_id"]).values())
    no_leak, _ = _no_leak(x, p1.json() if p1.content else {})
    ok = (p1.status_code != 200 or not m) and no_leak and p2.status_code == 422 and rows == 0
    return ok, f"잘못된 토큰 HTTP {p1.status_code}(반환 {len(m)}), 잘못된 request_id HTTP {p2.status_code}, restore_log {rows}행"


def case_ITP19(x):
    from starter import chat
    c = x.client("Lv1")
    orig = chat.search
    def broken(*a, **k):
        raise RuntimeError("통합 테스트 오류 주입: 검색 DB 연결 실패")
    chat.search = broken
    try:
        r = c.post("/api/chat", json={"query": x.query()})
    finally:
        chat.search = orig
    detail = r.json() if r.content else {}
    row = x.last_query_log(USERS["Lv1"])
    logs = ""
    for name in ("server_error.log", "audit_fallback.jsonl"):
        p = ROOT / "outputs" / name
        if p.exists():
            logs += p.read_text(encoding="utf-8", errors="ignore")[-20000:]
    ok1, msg1 = _no_leak(x, detail)
    ok2 = row is not None and row[0] == "error" and not any(v in (row[1] or "") for v in x.raw)
    ok3 = not any(v in logs for v in x.raw)
    return r.status_code == 500 and ok1 and ok2 and ok3, \
        f"HTTP {r.status_code}, 응답 {msg1}, query_log state={row[0] if row else None}, 오류 로그 원문 {'없음' if ok3 else '있음'}"


def case_ITP20(x):
    c = x.client("Lv2_admin")
    outs = [c.get(p) for p in ("/api/admin/docs/IT0071", "/api/admin/logs/query", "/api/admin/logs/restore")]
    payloads = [o.json() if o.status_code == 200 else {} for o in outs]
    ok, msg = _no_leak(x, *payloads)
    return all(o.status_code == 200 for o in outs) and ok, f"HTTP {[o.status_code for o in outs]}, {msg}"


def case_ITP21(x):
    c = x.client("Lv2")
    r, b = x.chat(c, x.query())
    c.post("/api/verify-password", json={"password": pw(USERS["Lv2"])})
    p = c.post("/api/restore-tokens", json={"request_id": b["request_id"], "tokens": x.tokens(b) or ["<PERSON_000000000000>"]})
    m = p.json().get("map", {}) if p.status_code == 200 else {}
    names = [t for t in m if t.startswith("<PERSON_")]
    return "hold", f"여러 인물 답변 복원: 이름 {len(names)}명 복원 (값은 기록하지 않음)"


def case_ITP22(x):
    c = x.client("Lv1")
    r, b = x.chat(c, x.query())
    c.post("/api/verify-password", json={"password": pw(USERS["Lv1"])})
    p = c.post("/api/partial-tokens", json={"request_id": b["request_id"], "tokens": x.tokens(b, "name") or ["<PERSON_000000000000>"]})
    m = p.json().get("map", {}) if p.status_code == 200 else {}
    return "hold", f"목록 문서 부분 공개: 이름 {len(m)}명 공개"


def run(run_dir, ids=None, resume=False, breaker=None):
    try:
        from fastapi.testclient import TestClient
    except ImportError:                                   # TestClient 는 httpx 가 필요하다
        raise SystemExit("권한 API 테스트에는 httpx 가 필요합니다: pip install httpx")
    from service.app import app
    from starter import chat
    run_dir = pathlib.Path(run_dir)
    gold = Gold()
    guard = getattr(chat._post_llm, "__self__", None)
    own_guard = not isinstance(guard, LLMGuard)          # 단독 실행이면 여기서 호출 관리를 설치
    if own_guard:
        guard = LLMGuard(run_dir).install()
    path = run_dir / "security.jsonl"
    prev = {r["id"]: r for r in jl_read(path)} if (resume and path.exists()) else {}
    keep = {k: v for k, v in prev.items() if v["result"] in ("통과", "실패", "판정 보류")}   # 미검증·오류도 다시
    print("\n[권한 API] ITP01~ITP22" + (f"  · 이미 결과 있음 {len(keep)}개는 건너뜀" if keep else ""))
    results = dict(prev)

    def save():
        order = [c["id"] for c in gold.access]
        jl_write(path, [results[i] for i in order if i in results])

    try:
        with TestClient(app):                              # lifespan (SESSION_SECRET 확인, 탐지 모델 로드)
            x = Ctx(app, gold, run_dir)

            def clean(text):                               # 결과 기록에 원문이 남지 않게
                for v in sorted(x.raw, key=len, reverse=True):
                    if v in text:
                        text = text.replace(v, "[원문]")
                return text
            for case in gold.access:
                if (ids and case["id"] not in ids) or case["id"] in keep:
                    continue
                fn = globals()[f"case_{case['id']}"]
                guard.context = case["id"]
                x.case = case
                x.events = []
                if sorted(CODE_USERS.get(case["id"], [])) != sorted(case.get("users") or []):
                    rec = {"id": case["id"], "scenario": case["scenario"], "required": case["required"], "result": "오류",
                           "expected": case["expect"],
                           "actual": f"정답지 사용자 {case.get('users')} ≠ 코드 사용자 {CODE_USERS.get(case['id'])}"}
                    results[case["id"]] = rec
                    save()
                    print(f"  {case['id']} 오류   {rec['actual']}")
                    continue
                failed0 = guard.stats["failed"]
                err, info = None, None
                try:
                    ok, actual = fn(x)
                    result = "판정 보류" if ok == "hold" else ("미검증" if ok is None else ("통과" if ok else "실패"))
                except Exception as exc:
                    err = exc
                    if is_exec_error(exc):
                        result, info = EXEC_ERROR, exec_error_info(exc)
                        actual = f"{type(exc).__name__}: {str(exc)[:200]}"
                    else:
                        result, actual = "오류", f"{type(exc).__name__}: {str(exc)[:200]}"
                if guard.stats["failed"] > failed0 and result != EXEC_ERROR:
                    # API 안에서 LLM 호출이 끝내 실패하면 서버가 오류 응답을 돌려준다 → 챗봇 판정이 아니라 실행 오류
                    err = guard.last_error
                    result, info = EXEC_ERROR, (err.info() if err else None)
                    actual = f"LLM 호출 실패로 판정 불가 ({actual})"
                if case["required"] == "판정 보류" and result in ("통과", "실패"):
                    result = "판정 보류"
                rec = {"id": case["id"], "scenario": case["scenario"], "required": case["required"], "result": result,
                       "expected": case["expect"], "actual": clean(str(actual))}
                if result != EXEC_ERROR:                       # 케이스마다 응답 원문 유출(X04)·감사 로그(X06)를 따로 판정
                    try:
                        rec["x04"], rec["x04_detail"] = x.x04()
                        rec["x06"], rec["x06_detail"] = x.x06()
                    except Exception as exc:
                        rec["x06"], rec["x06_detail"] = ("실행 오류" if is_exec_error(exc) else "오류"), type(exc).__name__
                if info:
                    rec["exec_error"] = info
                results[case["id"]] = rec
                save()                                     # 케이스마다 바로 저장
                print(f"  {case['id']} {result:6} {rec['actual']}")
                if breaker is not None:
                    if result == EXEC_ERROR:
                        breaker.fail(err or RuntimeError(actual), f"권한 API {case['id']}")
                    else:
                        breaker.ok()
    finally:
        guard.context = ""
        if own_guard:
            guard.uninstall()
    save()
    return list(results.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--ids", help="ITP01,ITP04 처럼 일부만")
    ap.add_argument("--resume", action="store_true", help="결과가 있는 케이스는 건너뛰고 실행 오류·미실행만")
    ap.add_argument("--api-config", default="R2", help="실행 설정 (엑셀 04 시트 E6 'API 평가 설정'과 같게)")
    a = ap.parse_args()
    from .common import CONFIGS, PHASE, apply_env, config_env, db_state
    if a.api_config not in CONFIGS:
        raise SystemExit(f"알 수 없는 --api-config: {a.api_config}")
    try:
        cur = db_state()["phase"]
    except Exception as exc:
        raise SystemExit(f"DB에 연결할 수 없습니다 ({type(exc).__name__}).")
    if cur != PHASE[a.api_config]:                        # 설정(인물사전 사용 여부)과 실제 DB 적재가 같아야 한다
        raise SystemExit(f"--api-config {a.api_config} 는 인물사전 {PHASE[a.api_config].upper()} 적재가 필요한데, "
                         f"지금 DB는 인물사전 {cur.upper()} 상태입니다. 맞게 다시 적재한 뒤 실행하세요.")
    apply_env(config_env(a.api_config))
    mp = pathlib.Path(a.run_dir) / "manifest.json"
    if mp.exists():                                       # 보고서가 API 행의 실행 설정을 알 수 있게
        m = json.loads(mp.read_text(encoding="utf-8"))
        m["api_config"] = a.api_config
        mp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    run(a.run_dir, set(a.ids.split(",")) if a.ids else None, resume=a.resume)


if __name__ == "__main__":
    main()
