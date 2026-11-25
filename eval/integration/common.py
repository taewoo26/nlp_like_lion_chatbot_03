"""통합 테스트 공통: 경로, 실행 설정(R0~R3), DB 상태 확인, 정답 로드, 토큰 계산, 결과 행 형식.

저장소 코드(starter, service, eval/*.py)는 import 해서 읽기만 하고 고치지 않는다.
"""
import csv
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]          # 저장소 루트
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

DATA = ROOT / "data" / "integration_v1"
OUT = ROOT / "outputs" / "it"

# 설계서 04 시트 '실행 설정'. 모든 설정에 공통 값을 명시해 .env 값과 섞이지 않게 한다.
BASE_ENV = {"SEARCH_MODE": "and", "SCORE_RATIO": "0.7", "RECHECK": "on", "REGION_META": "off", "ROLE_META": "off",
            "BM25_WEIGHT": "0.3", "BM25_CUTOFF": "on", "RERANK_TOP": "5", "PERSON_RULES": "v3"}
CONFIGS = {
    "R0": {"PERSON_DICT": "off", "BM25_WEIGHT": "0", "RERANK_TOP": "0"},
    "R1": {"PERSON_DICT": "on", "BM25_WEIGHT": "0", "RERANK_TOP": "0"},
    "R2": {"PERSON_DICT": "off"},
    "R3": {"PERSON_DICT": "on"},
    "R2c": {"PERSON_DICT": "off", "BM25_CUTOFF": "off"},
    "R2 + REGION_META": {"PERSON_DICT": "off", "REGION_META": "on"},
}
UNAVAILABLE = {"R2′": "개선 코드가 아직 없음", "R3′": "개선 코드가 아직 없음"}
PHASE = {c: ("on" if v["PERSON_DICT"] == "on" else "off") for c, v in CONFIGS.items()}
# 적재 때 쓰는 환경 (인물사전 OFF 적재는 지역 메타도 함께 저장해 'R2 + REGION_META'에 쓴다)
INGEST_ENV = {"off": {"PERSON_DICT": "off", "REGION_META": "on"},
              "on": {"PERSON_DICT": "on", "PERSON_RULES": "v3", "REGION_META": "on"}}

AREAS = {"P": "01. 개인정보 탐지·가명처리", "I": "02. 인물 판별", "Q": "03. 질문 처리", "S": "04. 검색",
         "A": "05. 답변 생성", "G": "06. 권한·보안", "X": "공통 검사"}
# 채점기 내부 행 형식. report.py 가 엑셀 04 시트 tblResult 열(EXCEL_RESULT_COLS)로 바꿔 results.csv 에 쓴다.
RESULT_COLS = ["실행 단위", "실행 ID", "split", "난이도", "실행 설정", "평가 영역", "평가 항목 ID", "평가 내용",
               "기대값", "실제값", "결과", "실패 원인 단계", "실행일", "비고", "실행 권한·계정"]
EXCEL_RESULT_COLS = ["실행 ID", "시나리오 ID", "케이스 ID", "실행 설정", "실행 권한·계정", "기대 결과", "실제 결과", "판정",
                     "실패 원인", "비고", "split", "난이도", "실행 단위", "평가 영역", "검증 키"]
EXCEL_SET_COLS = ["실행 ID", "질문 집합", "split", "실행 설정", "시나리오 ID", "지표", "측정값", "비교 기준값", "판정", "난이도"]
SET_COLS = ["질문 집합", "split", "실행 설정", "평가 항목 ID", "지표", "값", "비교 기준 값", "판정"]
JUDGE_RESULT = {"baseline": "기준선 기록", "hold": "판정 보류", "wait": "실행 대기"}


def config_env(name):
    if name in UNAVAILABLE:
        raise KeyError(f"{name}: {UNAVAILABLE[name]}")
    env = dict(BASE_ENV)
    env.update(CONFIGS[name])
    return env


def apply_env(env):
    """os.environ 을 바꾼다. 저장소 코드는 설정 값을 호출할 때마다 읽으므로 같은 프로세스에서 설정을 바꿀 수 있다."""
    for k, v in env.items():
        os.environ[k] = v


def jl_read(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def jl_write(path, rows, mode="w"):
    with open(path, mode, encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def csv_read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def csv_write(path, cols, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:      # 엑셀에서 바로 열리도록 BOM
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


class Gold:
    """정답지 묶음 (data/integration_v1 파생 파일)."""

    def __init__(self, root=DATA):
        self.root = pathlib.Path(root)
        self.cases = jl_read(self.root / "it_cases.jsonl")
        self.case = {c["qid"]: c for c in self.cases}
        self.docs = {d["id"]: d for d in jl_read(self.root / "it_docs_labeled.jsonl")}
        self.mentions = jl_read(self.root / "it_mentions_gold.jsonl")
        self.access = jl_read(self.root / "it_access_cases.jsonl")
        self.dialogs = jl_read(self.root / "it_dialogs.jsonl")
        src = self.root / "it_source"
        self.persons = {p["person_id"]: p for p in csv_read(src / "persons.csv")}
        self.exposure = csv_read(src / "exposure.csv")
        self.scenarios = {s["scenario_id"]: s for s in csv_read(src / "scenarios.csv")}

    def raw_values(self, doc_ids=None):
        """문서에 표시된 개인정보 원문 (이름 포함). 출력에 나오면 유출로 보는 값."""
        out = set()
        for did, d in self.docs.items():
            if doc_ids is not None and did not in doc_ids:
                continue
            for e in d["entities"]:
                if len(e["text"]) >= 2:
                    out.add(e["text"])
        return out


def token_of(label, value):
    """저장소와 같은 HMAC 키로 원문 값의 토큰을 계산한다 (정답 비교용)."""
    from starter import pii_pipeline
    return pii_pipeline.make_token(label, pii_pipeline.normalize_value(label, value))


def db_state():
    """현재 DB 상태: 적재 문서(통합 테스트·그 밖), 문서 ID 해시, 청크 수, 인물사전 사용 여부."""
    from starter import db
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT count(*), count(*) FILTER (WHERE doc_id LIKE 'IT%%'),
                              coalesce(md5(string_agg(doc_id, ',' ORDER BY doc_id)), '')
                       FROM (SELECT DISTINCT doc_id FROM rag.chunks) t""")
        n_docs, n_it, doc_hash = cur.fetchone()
        cur.execute("SELECT count(*) FROM rag.chunks")
        n_chunks = cur.fetchone()[0]
        try:
            cur.execute("SELECT count(*) FROM rag.look_up")
            n_lookup = cur.fetchone()[0]
        except Exception:
            conn.rollback()
            n_lookup = 0
    return {"docs": n_docs, "it_docs": n_it, "other_docs": n_docs - n_it, "doc_hash": doc_hash, "chunks": n_chunks,
            "phase": "on" if n_lookup else "off", "look_up": n_lookup}


def holdout_info(path):
    """함께 적재할 기존 문서(홀드아웃) 파일: 문서 수, 문서 ID 해시 (파일은 읽기만 한다)."""
    import hashlib
    path = pathlib.Path(path)
    ids = sorted(json.loads(l)["id"] for l in open(path, encoding="utf-8") if l.strip())
    return {"path": str(path), "docs": len(ids), "ids_sha": hashlib.md5(",".join(ids).encode()).hexdigest(),
            "content_sha": hashlib.sha256(path.read_bytes()).hexdigest(),
            "it_overlap": sorted(i for i in ids if i.startswith("IT"))[:5], "ids": ids}


def holdout_raw(run_dir, min_name=3, min_other=6):
    """함께 적재한 기존 문서(홀드아웃)의 개인정보 원문 → 유출 검사 대상 (manifest 의 holdout 경로)."""
    p = pathlib.Path(run_dir) / "manifest.json"
    if not p.exists():
        return {}
    h = json.loads(p.read_text(encoding="utf-8")).get("holdout") or {}
    path = pathlib.Path(h.get("path") or "")
    if not path.is_file():
        return {}
    out = {}
    for d in jl_read(path):
        for e in d.get("entities") or []:
            v = e.get("text") or d["text"][e["start"]:e["end"]]
            if (e["label"] == "person" and len(v) >= min_name) or (e["label"] != "person" and len(v) >= min_other):
                out[v] = e["label"]
    return out


# ------------------------------------------------------------------ 실행 대상 목록
# run.py 가 실행 전에 manifest['plan'] 에 '설정 × 영역 × 질문' 대상을 확정해 둔다 (세션마다 합집합).
# 채점·보고서는 결과 파일이 아니라 이 목록을 기준으로 완료·실행 오류·미실행을 센다.
def load_plan(run_dir):
    p = pathlib.Path(run_dir) / "manifest.json"
    if not p.exists():
        return None
    plan = json.loads(p.read_text(encoding="utf-8")).get("plan")
    if not plan:
        return None
    return {"search": {c: set(v) for c, v in plan.get("search", {}).items()},
            "answer": {c: set(v) for c, v in plan.get("answer", {}).items()},
            "security": set(plan.get("security", []))}


def plan_targets(run_dir, area, gold_cases):
    """area('search'|'answer') 의 {설정: [case]} — plan 이 없으면 결과 파일이 있는 설정의 전체 대상."""
    plan = load_plan(run_dir)
    key = "search_configs" if area == "search" else "answer_configs"
    if plan is not None:
        return {cfg: [c for c in gold_cases if c["qid"] in q and cfg in c[key]] for cfg, q in plan[area].items()}
    tdir = pathlib.Path(run_dir) / "traces"
    cfgs = [p.stem.split("_", 1)[1] for p in sorted(tdir.glob(f"{area}_*.jsonl"))] if tdir.exists() else []
    return {cfg: [c for c in gold_cases if cfg in c[key]] for cfg in cfgs}


def result_row(unit, run_id, split, diff, cfg, item_id, content, expected, actual, result, stage="", note="", date="",
               level=""):
    """결과 한 행 (내부 형식). run_id 는 케이스 ID(질문 ITQ·API ITP·적재 ITL), level 은 실행 권한(Lv0~2)·계정."""
    area = AREAS.get(item_id[:1], "공통 검사") if item_id[:1] in AREAS else item_id
    return dict(zip(RESULT_COLS, [unit, run_id, split, diff, cfg, area, item_id, content, expected, actual,
                                  result, stage if result == "실패" else "", date, note, level]))


def set_row(qset, split, cfg, item_id, metric, value, ref="", judge=""):
    if isinstance(value, float):
        value = round(value, 3)
    return dict(zip(SET_COLS, [qset, split, cfg, item_id, metric, value, ref, judge]))


# ------------------------------------------------------------------ 실행 상태
# 결과 행의 '결과' 값. 실행 오류·미실행은 챗봇 성능(통과율)에 넣지 않는다.
EXEC_ERROR, NOT_RUN = "실행 오류", "미실행"
MANUAL = "수동 확인"          # 자동 채점으로 단정할 수 없음 (부정 표현과 함께 나온 사실 등). 통과율에서 제외
NOT_IMPL = "채점 미구현"       # 설계에 있지만 채점 코드가 없는 시나리오


def is_exec_error(exc):
    """챗봇의 오답이 아니라 실행 환경 문제인 예외 (LLM 호출 제한·타임아웃·네트워크·DB 연결)."""
    if isinstance(exc, LLMCallError):
        return True
    try:
        import requests
        if isinstance(exc, requests.RequestException):
            return True
    except ImportError:
        pass
    try:
        import psycopg
        if isinstance(exc, psycopg.OperationalError):
            return True
    except ImportError:
        pass
    return isinstance(exc, (ConnectionError, TimeoutError))


def exec_error_info(exc):
    if isinstance(exc, LLMCallError):
        return exc.info()
    return {"kind": "environment", "status": None, "code": type(exc).__name__, "message": str(exc)[:300]}


def latest(rows, key="qid"):
    """같은 키가 여러 번 기록됐으면 마지막 행 (이어서 실행하면 기록이 뒤에 붙는다)."""
    return {r[key]: r for r in rows}


# ------------------------------------------------------------------ LLM 호출 관리
# 저장소의 starter.chat._post_llm 은 고치지 않고, 평가 실행 중에만 LLMGuard.post 로 바꿔 끼운다.
# (권한 API 테스트도 같은 프로세스에서 chat 을 부르므로 같이 적용된다)
#
# 환경변수 (CLOVA Studio HCX-007 기준. 테스트 API 키 60 QPM·60,000 TPM, 서비스 API 키 180 QPM·300,000 TPM)
#   IT_LLM_QPM=50          분당 요청 수 상한 (한도보다 조금 낮게)
#   IT_LLM_TPM=50000       분당 토큰 상한. CLOVA는 입력 토큰 + 최대 출력 토큰 설정값을 합쳐 센다
#   IT_LLM_EST_OUTPUT=1024 최대 출력 토큰을 요청에 지정하지 않았을 때 한 요청에 잡을 출력 토큰 수
#   IT_LLM_RETRIES=6       일시적 오류(429·5xx·타임아웃) 재시도 횟수
#   IT_LLM_MAX_WAIT=90     재시도 간격(지수 백오프)의 최대 초. 서버가 Retry-After 로 준 시간은 줄이지 않는다
#   IT_LLM_MAX_HINT=600    서버가 이보다 길게 기다리라고 하면 사용량 한도 소진으로 보고 실행을 멈춘다
LLM_LOG = "llm_calls.jsonl"


class LLMCallError(RuntimeError):
    """LLM 호출 실패. kind: rate_limit / overloaded / timeout / network / server (일시적)
    quota / auth (사용량·결제·인증 → 실행 중단), bad_request (그 요청만 실패, 재시도 안 함)"""
    FATAL = ("quota", "auth")

    def __init__(self, kind, status=None, code=None, message=""):
        self.kind, self.status, self.code, self.message = kind, status, code, message or ""
        super().__init__(f"LLM {kind} (HTTP {status}, code {code}): {self.message[:200]}")

    @property
    def fatal(self):
        return self.kind in self.FATAL

    def info(self):
        return {"kind": self.kind, "status": self.status, "code": self.code, "message": self.message[:300]}


def _env_num(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def _duration(v):
    """'23s', '1m2s', '500ms', '2' → 초"""
    import re
    if v is None:
        return None
    v = str(v).strip()
    try:
        return float(v)
    except ValueError:
        pass
    total, found = 0.0, False
    for num, unit in re.findall(r"([\d.]+)\s*(ms|h|m|s)", v):
        found = True
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if found else None


def _error_detail(r):
    """응답 본문에서 오류 코드·메시지 (OpenAI 형식 error.{code,type,message}, CLOVA 형식 status.{code,message})"""
    try:
        body = r.json()
    except Exception:
        return None, (r.text or "")[:300]
    if not isinstance(body, dict):
        return None, str(body)[:300]
    err = body.get("error") if isinstance(body.get("error"), dict) else None
    st = body.get("status") if isinstance(body.get("status"), dict) else None
    code = (err or {}).get("code") or (err or {}).get("type") or (st or {}).get("code")
    msg = (err or {}).get("message") or (st or {}).get("message") or str(body)
    return (str(code) if code is not None else None), str(msg)[:300]


def classify_http(status, code, message):
    c, m = (code or "").lower(), (message or "").lower()
    if any(w in c or w in m for w in ("insufficient_quota", "quota", "billing", "credit", "결제", "잔액")) \
            or c == "40170" or "subscription" in m:
        return "quota"
    if status == 429:
        return "overloaded" if c == "42902" or "overloaded" in m else "rate_limit"
    if status in (401, 403):
        return "auth"
    if status == 408:
        return "timeout"
    if status >= 500:
        return "server"
    return "bad_request"


class LLMGuard:
    RETRYABLE = ("rate_limit", "overloaded", "timeout", "network", "server")

    def __init__(self, run_dir=None):
        import threading
        self.qpm = max(_env_num("IT_LLM_QPM", 50), 1)
        self.tpm = max(_env_num("IT_LLM_TPM", 50000), 1)
        self.est_output = int(_env_num("IT_LLM_EST_OUTPUT", 1024))
        self.retries = int(_env_num("IT_LLM_RETRIES", 6))
        self.max_wait = _env_num("IT_LLM_MAX_WAIT", 90)          # 지수 백오프 상한 (서버 지정 대기에는 적용 안 함)
        self.max_hint = _env_num("IT_LLM_MAX_HINT", 600)         # 서버 지정 대기가 이보다 길면 한도 소진으로 보고 중단
        self.log_path = (pathlib.Path(run_dir) / LLM_LOG) if run_dir else None
        self.context = ""                  # 지금 실행 중인 항목 (예: R2/ITQ001), 기록용
        self.lock = threading.Lock()
        self.window = []                   # 최근 60초 (보낸 시각, 예상 토큰)
        self.last_sent = 0.0
        self.blocked_until = 0.0           # 응답 헤더로 알게 된 한도 재설정 시각
        self.stats = {"calls": 0, "ok": 0, "retries": 0, "failed": 0, "waited_s": 0.0, "by_kind": {}}
        self.last_error = None
        self._orig = None

    def settings(self):
        return {"qpm": self.qpm, "tpm": self.tpm, "est_output": self.est_output, "retries": self.retries,
                "max_wait": self.max_wait, "max_hint": self.max_hint}

    # -- 설치·해제
    def install(self):
        from starter import chat
        if self._orig is None:
            self._orig = chat._post_llm
            chat._post_llm = self.post
        return self

    def uninstall(self):
        if self._orig is not None:
            from starter import chat
            chat._post_llm = self._orig
            self._orig = None

    # -- 내부
    def _log(self, **rec):
        if self.log_path:
            import time as _t
            rec = {"ts": _t.strftime("%Y-%m-%d %H:%M:%S"), "context": self.context, **rec}
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _sleep(self, sec, why, cap=True):
        """cap=False: 서버가 알려 준 대기 시간(Retry-After·재설정 시각)은 줄이지 않는다."""
        import time as _t
        if sec <= 0:
            return
        if cap:
            sec = min(sec, self.max_wait)
        self.stats["waited_s"] = round(self.stats["waited_s"] + sec, 1)
        if sec >= 3:
            print(f"    (LLM 대기 {sec:.0f}초: {why})", flush=True)
        _t.sleep(sec)

    def _estimate(self, payload):
        chars = sum(len(str(m.get("content", ""))) for m in payload.get("messages", []))
        out = payload.get("max_completion_tokens") or payload.get("max_tokens") or self.est_output
        return chars + int(out)                 # 한글은 대략 글자당 1토큰 이하라 넉넉하게 잡는다

    def _pace(self, est):
        import time as _t
        with self.lock:
            now = _t.time()
            while self.blocked_until > now:
                self._sleep(self.blocked_until - now, "서버가 알려 준 대기 시간", cap=False)
                now = _t.time()
            now = _t.time()
            gap = 60.0 / self.qpm - (now - self.last_sent)
            if gap > 0:
                self._sleep(gap, "분당 요청 수")
            now = _t.time()
            self.window = [(t, n) for t, n in self.window if now - t < 60]
            while self.window and sum(n for _, n in self.window) + est > self.tpm:
                self._sleep(60 - (now - self.window[0][0]) + 0.1, "분당 토큰 수")
                now = _t.time()
                self.window = [(t, n) for t, n in self.window if now - t < 60]
            self.last_sent = _t.time()
            self.window.append((self.last_sent, est))

    def _read_headers(self, r, est):
        import time as _t
        h = r.headers
        try:
            rem_q = h.get("x-ratelimit-remaining-requests")
            rem_t = h.get("x-ratelimit-remaining-tokens")
            waits = []
            if rem_q is not None and int(float(rem_q)) <= 0:
                waits.append(_duration(h.get("x-ratelimit-reset-requests")) or 0)
            if rem_t is not None and int(float(rem_t)) < est:
                waits.append(_duration(h.get("x-ratelimit-reset-tokens")) or 0)
            if waits and max(waits) > 0:
                self.blocked_until = max(self.blocked_until, _t.time() + max(waits))
        except (TypeError, ValueError):
            pass

    @staticmethod
    def _retry_after(r):
        h = r.headers
        for k in ("retry-after-ms",):
            if h.get(k):
                try:
                    return float(h[k]) / 1000
                except ValueError:
                    pass
        if h.get("retry-after"):
            v = _duration(h["retry-after"])
            if v is None:                               # HTTP 날짜 형식
                try:
                    import email.utils, time as _t
                    v = email.utils.parsedate_to_datetime(h["retry-after"]).timestamp() - _t.time()
                except Exception:
                    v = None
            if v is not None:
                return max(v, 0)
        resets = [_duration(h.get(k)) for k in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens")]
        resets = [v for v in resets if v]
        return max(resets) if resets else None

    # -- chat._post_llm 대체
    def post(self, payload, retries=None):
        import random
        import requests
        from starter import config
        url = config.LLM_API_BASE.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {config.LLM_API_KEY}"}
        est = self._estimate(payload)
        n_retry = self.retries if retries is None else max(self.retries, retries)
        self.stats["calls"] += 1
        err = None
        for attempt in range(n_retry + 1):
            self._pace(est)
            wait_hint = None
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=120)
            except requests.Timeout as exc:
                err = LLMCallError("timeout", None, type(exc).__name__, str(exc))
            except requests.RequestException as exc:
                err = LLMCallError("network", None, type(exc).__name__, str(exc))
            else:
                self._read_headers(r, est)
                if r.status_code < 400:
                    self.stats["ok"] += 1
                    return r
                code, msg = _error_detail(r)
                err = LLMCallError(classify_http(r.status_code, code, msg), r.status_code, code, msg)
                wait_hint = self._retry_after(r)
            k = self.stats["by_kind"]
            k[err.kind] = k.get(err.kind, 0) + 1
            retry = err.kind in self.RETRYABLE and attempt < n_retry
            wait = 0.0
            if retry and wait_hint is not None and wait_hint > self.max_hint:
                # 서버가 몇 분 넘게 기다리라고 하면 일시적 제한이 아니라 사용량 한도로 보고 멈춘다
                err = LLMCallError("quota", err.status, err.code,
                                   f"서버 대기 요구 {wait_hint:.0f}초 (IT_LLM_MAX_HINT {self.max_hint:.0f}초 초과): {err.message}")
                retry = False
            if retry:
                wait = wait_hint if wait_hint is not None else min(2 ** (attempt + 1), self.max_wait)
                wait += random.uniform(0, 1)
            self._log(attempt=attempt + 1, **err.info(), retry=retry, wait=round(wait, 1))
            print(f"    LLM {err.status or '-'} {err.code or ''} [{err.kind}] {err.message[:120]}"
                  + (f" → {wait:.0f}초 후 재시도 ({attempt + 1}/{n_retry})" if retry else " → 재시도 안 함"), flush=True)
            if not retry:
                break
            self.stats["retries"] += 1
            if wait_hint is not None:          # 서버 지정 대기: 줄이지 않고, 다음 호출들도 그때까지 막는다
                import time as _t
                self.blocked_until = max(self.blocked_until, _t.time() + wait)
                self._sleep(wait, f"{err.kind} 재시도 (서버 지정)", cap=False)
            else:
                self._sleep(wait, f"{err.kind} 재시도")
        self.stats["failed"] += 1
        self.last_error = err
        raise err
