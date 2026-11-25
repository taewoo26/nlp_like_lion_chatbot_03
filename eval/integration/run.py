"""통합 테스트 한 번에 실행: (적재) → 질문 실행 → 인물 판정 수집 → 권한 API → 채점·보고.

저장소 루트에서:
    python -m eval.integration.run --rebuild-db --yes           # DB를 비우고 기존 문서 + 통합 테스트 84건을 OFF·ON 두 번 적재, R0~R3 전부
    python -m eval.integration.run                              # 지금 DB 상태에 맞는 설정만 실행 + 채점
    python -m eval.integration.run --configs R2,R3 --limit 10   # 일부 설정·질문만 빠르게
    python -m eval.integration.run --only search                # 검색 평가만 (LLM 호출 없음)
    python -m eval.integration.run --run-id <ID> --resume --rebuild-db --yes --only answer   # 같은 실행에 답변만 이어서
                                                                (DB가 맞지 않는 적재만 다시 적재)
    python -m eval.integration.run --run-id <ID> --resume       # 중단된 실행 이어서
    python -m eval.integration.report --run outputs/it/<ID>     # 저장된 기록으로 다시 채점 (LLM·DB 호출 없음)
    python -m pytest eval/integration/test_scoring.py -q        # 채점 로직 테스트 (DB·LLM 없음)

테스트 대상 (적재)
- DB를 비운 뒤 기존 문서(--holdout, 기본 data/holdout/holdout_labeled.jsonl)를 적재하고, 통합 테스트 84건을
  --reset 없이 추가 적재한다 (원본 파일은 읽기만 하고 합친 파일을 만들지 않는다). 인물사전 OFF·ON 두 적재 모두 같은 문서.
- 검색은 전체 적재 문서 대상. 탐지·인물 판별 채점은 정답 표시가 있는 84건 기준이고, 기존 문서 인물과의 오병합도 센다.
- 적재 뒤 84건의 실제 가명처리 본문(masked_<적재>.jsonl)을 남겨 원문 잔존·토큰 검사에 쓴다.
- DB 확인: 통합 테스트 문서 84건, 기존 문서 수 = 홀드아웃 문서 수, 인물사전 ON/OFF. 이어서 실행할 때는 문서 ID 해시·청크 수·
  인물 연결 수까지 이전 기록과 비교한다.

데이터: 완성된 JSONL(data/integration_v1)을 그대로 쓴다. 실행 전에 내용 해시를 it_manifest 기록과 대조한다.
원본(it_source)에서 다시 만들 때만 --prepare-data.
DB 안전장치: --rebuild-db 는 지울 DB에 홀드아웃·통합 테스트가 아닌 문서가 있으면 멈춘다 (--wipe-other-data 로만 진행).
적재 순서: 지금 DB와 같은 적재부터 실행하고, 남은 질문이 없는 적재는 재적재·실행을 생략한다.

답변 실행: 서비스 API(/api/chat)를 사용자 레벨 계정(Lv0·Lv1·Lv2)마다 호출한다 (R0~R3 472회).
다중 턴 대화(it_dialogs ITM01~05): 인물사전 ON 설정(R1·R3) × Lv0·Lv1·Lv2. 1턴 응답이 되묻기(need_more)면 화면처럼
2턴에 follow_up_of(1턴 request_id)를 붙여 보낸다. 되묻지 않았으면 2턴을 새 질문으로 보낸다 (대화는 1턴 실패로 채점). chat._answer 를 감싸
LLM 입력 문서·메타·재검사 결과를, 요청마다 audit.query_log 를 대조해 감사 로그(X06)를 남긴다. 권한 API는 --api-config(기본 R2).

실행 대상 목록과 중간 결과
- 실행 전에 '설정 × 영역(검색·답변·권한) × 질문' 대상을 manifest.json 의 plan 에 확정한다. 채점·보고서는 이 목록 기준으로
  완료·실행 오류·미실행을 센다 (결과 파일이 없는 설정도 미실행).
- 질문마다 실행 당시 키워드, BM25 적용 전 후보·BM25 뒤·임계값 뒤 후보(probe), LLM 입력 문서를 함께 저장한다.

이어서 실행 (--resume)
- 질문·권한 케이스마다 끝나는 즉시 저장한다. 정상 실행된 것은 건너뛰고, 실행 오류·미실행만 다시 실행한다.
- 이전 실행과 테스트 데이터·기존 문서·설정 환경값·실행 환경(모델·임베딩)·코드 커밋이 다르면 멈춘다 (--allow-mismatch 로 무시).
- DB 상태가 이전 기록과 다르면 그 설정은 건너뛴다. --rebuild-db 를 함께 주면 DB가 맞지 않을 때만 다시 적재한다.
- 실행 오류가 --stop-after(기본 3)번 연속되거나 사용량·결제·인증 오류가 나면, 또는 Ctrl+C 로 멈추면
  기록과 manifest 를 저장하고 부분 보고서를 만든 뒤 이어서 실행할 명령을 알려 준다 (종료 코드 2 / 130).

LLM 호출 관리 (저장소 chat._post_llm 은 고치지 않고 평가 실행 중에만 바꿔 끼움, common.LLMGuard)
- 분당 요청·토큰 수를 미리 맞춘다: IT_LLM_QPM(기본 50), IT_LLM_TPM(기본 50000), IT_LLM_EST_OUTPUT(기본 1024)
  CLOVA Studio HCX-007: 테스트 API 키 60 QPM·60,000 TPM, 서비스 API 키 180 QPM·300,000 TPM.
  TPM은 입력 토큰 + 최대 출력 토큰 설정값(LLM_MAX_TOKENS)으로 센다. 토큰 수는 글자 수로 넉넉하게 추정하고, 응답 헤더의
  남은 토큰이 모자라면 재설정 시각까지 기다린다.
- Retry-After·x-ratelimit-reset-* 로 서버가 준 대기 시간은 줄이지 않고 따른다. IT_LLM_MAX_HINT(기본 600초)보다 길면
  사용량 한도 소진으로 보고 멈춘다. 오류 코드·메시지를 출력하고 llm_calls.jsonl 에 남긴다.
- 일시적 제한(429 42901·42902, 5xx, 타임아웃)은 재시도(IT_LLM_RETRIES, 기본 6). 사용량·결제 한도와 인증 오류
  (insufficient_quota, 40170, 401·403)는 재시도하지 않고 실행을 멈춘다. 400(요청 오류)은 그 질문만 실행 오류.

실행 오류와 성능 분리
- 429·타임아웃·DB 연결 등은 챗봇 오답이 아니라 '실행 오류'로 기록하고 성능 지표에서 뺀다. 결과 값은
  통과 / 실패 / 수동 확인 / 실행 오류 / 미실행 (+ 기준선 기록 등). 보고서에 실행 완료율을 따로 표시한다.
- 필수 보안 판정은 필수 권한 케이스와 설계상 R0~R3 전체 답변의 유출 검사가 다 끝나야 완료로 본다.

결과: outputs/it/<실행ID>/
  manifest.json            실행 조건 (설정별 환경값, 기존 문서, 실행 환경, DB 상태, 커밋, 데이터 해시, 실행 대상 목록, 세션, 상태)
  traces/search_<설정>.jsonl  질문별 검색 기록 (k=20, LLM 없음, probe 포함)
  traces/answer_<설정>.jsonl  질문별 답변 기록 (k=3, LLM 호출, LLM 입력 문서·probe 포함). 실행 오류 행은 exec_error
  traces/dialog_<설정>.jsonl  대화별 기록 (턴마다 API 응답 전체·내부 상태·감사 로그 대조, key = 대화ID|레벨)
  llm_calls.jsonl          LLM 오류·재시도 기록 (HTTP 상태, 오류 코드·메시지, 대기 시간)
  pseudo_off/on.jsonl      84건 탐지·가명처리 결과 · masked_off/on.jsonl 84건 실제 가명처리 본문
  person_rows.json         인물사전 ON 적재의 판정 결과 (vault.person_info, 기존 문서 포함)
  security.jsonl           권한 API 22개 케이스 결과
  report/                  채점 결과 (report.py)
저장소 코드는 import 해서 쓰기만 하고 고치지 않는다.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

from .common import (CONFIGS, DATA, DIALOG_CONFIGS, DIALOG_LEVELS, INGEST_ENV, OUT, PHASE, ROOT, UNAVAILABLE, LLMCallError, LLMGuard, apply_env,
                     config_env, db_state, exec_error_info, holdout_info, holdout_raw, is_exec_error, jl_read, jl_write,
                     latest)

DEFAULT_CONFIGS = ["R0", "R1", "R2", "R3"]


def sh(cmd, env=None, check=True):
    print("  $", " ".join(cmd), flush=True)
    full = None
    if env:
        import os
        full = dict(os.environ)
        full.update(env)
    return subprocess.run(cmd, cwd=ROOT, env=full, check=check)


DERIVED = ("it_docs.jsonl", "it_docs_labeled.jsonl", "it_cases.jsonl", "it_access_cases.jsonl", "it_manifest.json")


def prepare(skip_validate):
    """--prepare-data 일 때만: 원본(it_source) → 파생 JSONL 재생성, 정합성 검사."""
    print("\n[준비] 데이터 재생성: 원본 → 파생 JSONL, 정합성 검사")
    py = sys.executable
    sh([py, "eval/integration/build_data.py", "--root", str(DATA)])
    if not skip_validate:
        sh([py, "eval/integration/validate_data.py", "--root", str(DATA), "--repo", "."])


def check_data():
    """완성된 파생 JSONL 이 있고, 내용이 it_manifest 에 기록된 해시와 같은지 확인한다 → 실제 내용 해시."""
    import hashlib
    missing = [f for f in DERIVED if not (DATA / f).exists()]
    if missing:
        raise SystemExit(f"완성된 테스트 데이터가 없습니다: {', '.join(missing)}\n"
                         f"  python -m eval.integration.run --prepare-data ... 로 한 번 만들어 주세요.")
    recorded = json.loads((DATA / "it_manifest.json").read_text(encoding="utf-8")).get("derived_sha256") or {}
    actual = {n: hashlib.sha256((DATA / n).read_bytes()).hexdigest() for n in recorded if (DATA / n).exists()}
    bad = sorted(n for n, h in recorded.items() if actual.get(n) != h)
    if bad:
        raise SystemExit(f"테스트 데이터 내용이 it_manifest 기록과 다릅니다: {', '.join(bad)}\n"
                         f"  파생 파일을 직접 고쳤거나 일부만 다시 만든 경우입니다. 원본(it_source)을 고친 뒤 --prepare-data 로 다시 만드세요.")
    return actual


def _db_doc_ids():
    """지금 DB의 문서 ID 집합 (연결이 안 되면 None)."""
    try:
        from starter import db
        with db.rag_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT doc_id FROM rag.chunks")
            return {r[0] for r in cur.fetchall()}
    except Exception:
        return None


def _project_volumes(project):
    """해당 Docker 프로젝트에 남아 있는 볼륨 이름 (확인 실패 시 None)."""
    try:
        r = subprocess.run(["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
                           capture_output=True, text=True, timeout=30)
        return [v for v in r.stdout.split() if v] if r.returncode == 0 else None
    except Exception:
        return None


def check_wipe_safe(holdout, allow_other, project):
    """DB를 지우기 전 안전장치: 기존 문서(홀드아웃)와 통합 테스트 문서가 아닌 문서가 있으면 멈춘다.
    다른 데이터가 든 개발 DB를 실수로 지우지 않도록 (--wipe-other-data 로만 넘어감).
    DB에 연결할 수 없으면 내용을 확인할 수 없으므로, 지울 볼륨이 남아 있는 한 멈춘다."""
    ids = _db_doc_ids()
    if ids is None:
        vols = _project_volumes(project)
        if vols == [] or allow_other:
            return                                    # 지울 볼륨이 없거나(첫 실행) 사용자가 허용함
        what = f"볼륨 {len(vols)}개 ({', '.join(vols[:3])})" if vols else "볼륨 확인 실패"
        raise SystemExit(f"DB에 연결할 수 없어 안의 문서를 확인하지 못했습니다 (Docker 프로젝트 '{project}', {what}).\n"
                         f"  멈춘 개발 DB일 수 있어 지우지 않습니다. DB를 켠 뒤 다시 실행하거나, 테스트 전용 "
                         f"--compose-project 를 쓰거나, 지워도 되면 --wipe-other-data 를 붙이세요.")
    if not ids:
        return
    known = set(holdout["ids"]) | {f"IT{i:04d}" for i in range(1, 85)}
    other = sorted(ids - known)
    if other and not allow_other:
        raise SystemExit(f"지울 DB에 테스트와 관계없는 문서 {len(other)}건이 있습니다 (예: {', '.join(other[:5])}).\n"
                         f"  개발 DB일 수 있습니다. 테스트 전용 Docker 프로젝트(--compose-project)를 쓰거나, 지워도 되면 "
                         f"--wipe-other-data 를 붙이세요.")


def rebuild_db(phase, project, run_dir, holdout, allow_other=False, keep_dumps=False):
    """DB를 비우고 기존 문서(홀드아웃)를 적재한 뒤 통합 테스트 84건을 추가 적재한다 (--reset 없는 추가 적재).
    keep_dumps: 권한 API 용 재적재 — 질문 실행 때 수집한 가명처리 본문·인물 판정 기록은 그대로 둔다."""
    check_wipe_safe(holdout, allow_other, project)
    for stale in [] if keep_dumps else [run_dir / f"masked_{phase}.jsonl"] + ([run_dir / "person_rows.json"] if phase == "on" else []):
        stale.unlink(missing_ok=True)                 # 새 적재 기준으로 다시 수집
    print(f"\n[적재] DB 초기화 → 기존 문서 {holdout['docs']}건 적재 → 통합 테스트 84건 추가 적재 (인물사전 {phase.upper()})")
    base = ["docker", "compose", "-p", project, "--env-file", ".env", "-f", "infra/compose.yaml"]
    sh(base + ["down", "-v"])
    sh(base + ["up", "-d", "--wait"])
    sh([sys.executable, "-m", "starter.ingest", "--data", holdout["path"]], env=INGEST_ENV[phase])
    sh([sys.executable, "-m", "starter.ingest", "--data", str(DATA / "it_docs.jsonl")], env=INGEST_ENV[phase])
    out = ROOT / "outputs"
    shutil.copy(out / "pseudo_it_docs.jsonl", run_dir / f"pseudo_{phase}.jsonl")
    hp = out / f"pseudo_{pathlib.Path(holdout['path']).stem}.jsonl"
    if hp.exists():
        shutil.copy(hp, run_dir / f"pseudo_holdout_{phase}.jsonl")
    rep = out / "person_resolution_pseudo_it_docs.json"
    if phase == "on" and rep.exists():
        shutil.copy(rep, run_dir / "person_resolution_summary.json")


class SearchProbe:
    """검색 한 번의 중간 결과를 남긴다 (저장소 함수를 평가 실행 중에만 감싼다).
    terms: 실행 당시 질문 키워드, bm25_applied, before_bm25: BM25 적용 전 후보(문서, 점수),
    after_bm25: BM25 합산·컷오프 후, after_threshold: 상대 임계값 후 최종 후보."""

    def __enter__(self):
        from starter import search as S
        from starter.bm25 import extract_terms, has_keywords
        self.S, self.rec = S, {}
        self.orig = (S._apply_bm25, S._apply_threshold)
        self.extract, self.has_kw = extract_terms, has_keywords

        def bm25(masked_q, docs):
            terms = self.extract(masked_q)
            self.rec.update(terms=sorted(terms), bm25_applied=bool(S._bm25_weight() > 0 and len(docs) > 1
                                                                    and self.has_kw(terms)),
                            before_bm25=[[d["doc_id"], round(float(d.get("score") or 0), 4)] for d in docs])
            out = self.orig[0](masked_q, docs)
            self.rec["after_bm25"] = [d["doc_id"] for d in out]
            return out

        def threshold(docs):
            out = self.orig[1](docs)
            self.rec["after_threshold"] = [d["doc_id"] for d in out]
            return out

        S._apply_bm25, S._apply_threshold = bm25, threshold
        return self

    def take(self, masked_q=None):
        rec, self.rec = self.rec, {}
        if "terms" not in rec and masked_q:                 # BM25 단계 전에 끝난 검색 (지역 되묻기 등)
            rec["terms"] = sorted(self.extract(masked_q))
        return rec

    def __exit__(self, *exc):
        self.S._apply_bm25, self.S._apply_threshold = self.orig


def _meta(meta):
    keep = ("regions", "ask_region", "roles", "excluded", "person_candidates", "ask_person", "person_scope")
    return {k: meta.get(k) for k in keep}


def _done(path, key="qid"):
    """정상 실행된 질문 (실행 오류 행은 다시 실행 대상). 답변은 key = '질문|레벨'."""
    if not path.exists():
        return set()
    rows = [r for r in jl_read(path) if key in r]
    return {q for q, r in latest(rows, key).items() if "exec_error" not in r}


def phase_todo(group, cases, run_dir, do_search, do_answer, dialogs=()):
    """이 적재(설정 묶음)에서 아직 정상 실행되지 않은 검색·답변·대화 수."""
    n = 0
    for cfg in group:
        if dialogs and cfg in DIALOG_CONFIGS:
            done = _done(run_dir / "traces" / f"dialog_{cfg}.jsonl", "key")
            n += sum(1 for d in dialogs for lv in DIALOG_LEVELS if answer_key(d["id"], lv) not in done)
        if do_search:
            done = _done(run_dir / "traces" / f"search_{cfg}.jsonl")
            n += sum(1 for c in cases if cfg in c["search_configs"] and c["qid"] not in done)
        if do_answer:
            done = _done(run_dir / "traces" / f"answer_{cfg}.jsonl", "key")
            n += sum(1 for c in cases if cfg in c["answer_configs"] for lv in c["users"]
                     if answer_key(c["qid"], lv) not in done)
    return n


class StopRun(Exception):
    """연속 실행 오류 또는 복구할 수 없는 오류 (사용량·결제·인증) → 기록을 저장하고 멈춘다."""


class Breaker:
    def __init__(self, limit):
        self.limit, self.streak = limit, 0

    def ok(self):
        self.streak = 0

    def fail(self, exc, where):
        self.streak += 1
        if isinstance(exc, LLMCallError) and exc.fatal:
            raise StopRun(f"{where}: 복구할 수 없는 LLM 오류 [{exc.kind}] HTTP {exc.status} {exc.code}: {exc.message[:200]}")
        if self.streak >= self.limit:
            raise StopRun(f"{where}: 실행 오류가 {self.streak}번 연속 발생 ({type(exc).__name__}: {str(exc)[:200]})")


def run_questions(cfg, cases, run_dir, resume, do_search, do_answer, guard, breaker):
    with SearchProbe() as probe:
        _run_questions(cfg, cases, run_dir, resume, do_search, do_answer, guard, breaker, probe)


def _run_questions(cfg, cases, run_dir, resume, do_search, do_answer, guard, breaker, probe):
    from starter import search as S
    apply_env(config_env(cfg))
    tdir = run_dir / "traces"
    tdir.mkdir(exist_ok=True)
    sp, ap = tdir / f"search_{cfg}.jsonl", tdir / f"answer_{cfg}.jsonl"
    done_s, done_a = _done(sp), _done(ap, "key")
    todo_s = [c for c in cases if do_search and cfg in c["search_configs"] and c["qid"] not in done_s]
    todo_a = [(c, lv) for c in cases if do_answer and cfg in c["answer_configs"]       # 사용자 레벨마다 한 번
              for lv in c["users"] if answer_key(c["qid"], lv) not in done_a]
    print(f"\n[질문] {cfg}: 검색 {len(todo_s)}개, 답변 {len(todo_a)}개 (LLM 호출)"
          + (f"  · 이미 정상 실행: 검색 {len(done_s)}, 답변 {len(done_a)}" if resume and (done_s or done_a) else ""))
    for c in todo_s:
        t0 = time.perf_counter()
        row = {"qid": c["qid"], "config": cfg}
        probe.take()
        try:
            masked_q, q_tokens, docs, meta = S.search(c["query"], 20, with_meta=True)
            row.update(masked_query=masked_q, query_tokens=q_tokens, ranked_doc_ids=[d["doc_id"] for d in docs],
                       meta=_meta(meta), probe=probe.take(masked_q))
            err = None
        except Exception as exc:
            err = exc
            if is_exec_error(exc):
                row["exec_error"] = exec_error_info(exc)
            else:                                           # 저장소 코드의 예외는 챗봇 결함으로 채점
                row.update(error=type(exc).__name__, masked_query=getattr(exc, "masked_query", None),
                           query_tokens=getattr(exc, "query_tokens", None))
        row["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        jl_write(sp, [row], "a")                            # 질문마다 바로 저장
        if row.get("exec_error"):
            breaker.fail(err, f"{cfg}/{c['qid']} 검색")
        else:
            breaker.ok()
    if todo_a:
        run_answers_api(cfg, todo_a, ap, guard, breaker, probe)
    guard.context = ""


def answer_key(qid, level):
    return f"{qid}|{level}"


class AnswerSpy:
    """서비스 API 안에서 불리는 chat._answer 를 감싸 LLM 입력·메타·재검사 전 결과를 남긴다 (저장소 코드 수정 없음)."""

    def __enter__(self):
        from starter import chat
        self.chat, self.orig, self.cap = chat, chat._answer, {}

        def spy(masked_q, q_tokens, docs, meta):
            self.cap.update(meta=_meta(meta), llm_input_doc_ids=[d["doc_id"] for d in docs])
            res = self.orig(masked_q, q_tokens, docs, meta)
            self.cap["res"] = res
            return res
        chat._answer = spy
        return self

    def take(self):
        cap, self.cap = self.cap, {}
        return cap

    def __exit__(self, *exc):
        self.chat._answer = self.orig


def audit_check(request_id, user_id, level, api_state, api_doc_ids, raw_values, ui_state):
    """X06 질문 요청 감사 로그: query_log 가 요청당 1행이고, 사용자·레벨이 맞고, 상태·반환 문서가 실제 API 응답과 같고,
    masked_query 에 원문이 없음. 상태는 query_log 의 값(6종)을 서비스와 같은 규칙(ui_state)으로 바꿔 API 응답 state 와 비교한다.
    결과에는 원문을 남기지 않는다 (개수만)."""
    from starter import db
    with db.auth_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id, level, state, masked_query, returned_doc_ids FROM audit.query_log WHERE request_id = %s",
                    (request_id,))
        rows = cur.fetchall()
    out = {"rows": len(rows)}
    if len(rows) == 1:
        u, lv, st, mq, docs = rows[0]
        out.update(user_ok=u == user_id,
                   level_ok=int(lv if lv is not None else -1) == int(level[2:]) if level.startswith("Lv") else True,
                   state_ok=ui_state(st) == api_state, docs_ok=list(docs or []) == list(api_doc_ids),
                   raw_in_masked=sum(1 for v in raw_values if v and v in (mq or "")))
    return out


def follow_up_check(request_id, expected_prev):
    """2턴 감사 로그의 follow_up_of 가 1턴 request_id 인지. 열이 없는 옛 DB면 None (미검증)."""
    from starter import db
    try:
        with db.auth_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT follow_up_of FROM audit.query_log WHERE request_id = %s", (request_id,))
            rows = cur.fetchall()
    except Exception:
        return None
    return len(rows) == 1 and rows[0][0] == expected_prev


def run_dialogs_api(cfg, run_dir, dialogs, guard, breaker):
    """다중 턴 대화를 서비스 API 로 실행한다 (사용자 레벨마다). 화면 동작 그대로: 1턴 응답의 need_more 가 True 면
    2턴에 follow_up_of = 1턴 request_id 를 붙이고, 아니면 붙이지 않는다. 턴마다 API 응답 전체·내부 상태·감사 로그를 남긴다."""
    from fastapi.testclient import TestClient
    from service.app import app, _ui_state
    from .common import Gold
    from .security import USERS, pw
    apply_env(config_env(cfg))
    tdir = run_dir / "traces"
    tdir.mkdir(exist_ok=True)
    dp = tdir / f"dialog_{cfg}.jsonl"
    done = _done(dp, "key")
    todo = [(d, lv) for d in dialogs for lv in DIALOG_LEVELS if answer_key(d["id"], lv) not in done]
    print(f"\n[대화] {cfg}: {len(todo)}개 (대화 × 레벨, LLM 호출 최대 {2 * len(todo)}번)"
          + (f"  · 이미 정상 실행: {len(done)}" if done else ""))
    if not todo:
        return
    raw_values = Gold().raw_values() | set(holdout_raw(run_dir))
    with TestClient(app), AnswerSpy() as spy:
        clients = {}

        def client(level):
            if level not in clients:
                uid = USERS[level]
                c = TestClient(app)
                r = c.post("/api/login", json={"user_id": uid, "password": pw(uid)})
                if r.status_code != 200:
                    raise RuntimeError(f"{level}({uid}) 로그인 실패 HTTP {r.status_code}")
                clients[level] = c
            return clients[level]

        for d, level in todo:
            user_id = USERS[level]
            row = {"key": answer_key(d["id"], level), "dialog": d["id"], "level": level, "user_id": user_id,
                   "config": cfg, "turns": []}
            guard.context = f"{cfg}/{d['id']}/{level}"
            t0, err, prev = time.perf_counter(), None, None
            try:
                for i, q in enumerate(d["turns"]):
                    failed0 = guard.stats["failed"]
                    spy.take()
                    follow = prev["request_id"] if prev and prev.get("need_more") and prev.get("request_id") else None
                    payload = {"query": q, "restore": False, **({"follow_up_of": follow} if follow else {})}
                    r = client(level).post("/api/chat", json=payload)
                    body = r.json() if r.content else {}
                    res = spy.take().get("res") or {}
                    turn = {"turn": i + 1, "http": r.status_code, "follow_up_of": follow, "api_body": body,
                            "request_id": body.get("request_id"), "need_more": bool(body.get("need_more")),
                            "merged": bool(body.get("merged")), "clarify_code": body.get("clarify_code"),
                            "state": res.get("state") if r.status_code == 200 else "error",
                            "llm_input_doc_ids": [x["doc_id"] for x in res.get("docs") or []]}
                    if guard.stats["failed"] > failed0:              # LLM 호출이 끝내 실패 → 실행 오류
                        e = guard.last_error
                        row["exec_error"] = e.info() if e else {"kind": "llm", "message": f"HTTP {r.status_code}"}
                        err = e
                    elif r.status_code != 200:
                        turn["error"] = str(body.get("detail"))[:200]
                    if turn["request_id"] and "exec_error" not in row:
                        turn["audit"] = audit_check(turn["request_id"], user_id, level, body.get("state"),
                                                    [x.get("doc_id") for x in body.get("docs") or []], raw_values, _ui_state)
                        if follow:
                            turn["audit"]["follow_up_ok"] = follow_up_check(turn["request_id"], follow)
                    row["turns"].append(turn)
                    prev = turn
                    if "exec_error" in row:
                        break
            except Exception as exc:
                err = exc
                if is_exec_error(exc):
                    row["exec_error"] = exec_error_info(exc)
                else:
                    row.update(error=f"{type(exc).__name__}: {str(exc)[:150]}")
            row["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            jl_write(dp, [row], "a")                          # 대화마다 바로 저장
            if row.get("exec_error"):
                breaker.fail(err or RuntimeError(str(row["exec_error"])), f"{cfg}/{d['id']}/{level} 대화")
            else:
                breaker.ok()
    guard.context = ""


def run_answers_api(cfg, todo, ap, guard, breaker, probe):
    """답변: 서비스 API(/api/chat)를 사용자 레벨 계정마다 호출한다 → 사용자별 X04, 질문 요청 감사 로그(X06)까지 채점 가능.
    chat._answer 를 감싸 LLM 입력·메타를, 검색 함수를 감싸 BM25 단계 후보를 함께 남긴다."""
    from fastapi.testclient import TestClient
    from service.app import app, _ui_state
    from .common import Gold
    from .security import USERS, pw
    raw_values = Gold().raw_values() | set(holdout_raw(ap.parent.parent))
    level_user = {k: v for k, v in USERS.items() if k in ("Lv0", "Lv1", "Lv2")}
    total = len(todo)
    with TestClient(app), AnswerSpy() as spy:                       # lifespan 한 번 (탐지 모델 로드)
        clients = {}

        def client(level, user_id):
            if level not in clients:
                c = TestClient(app)
                r = c.post("/api/login", json={"user_id": user_id, "password": pw(user_id)})
                if r.status_code != 200:
                    raise RuntimeError(f"{level}({user_id}) 로그인 실패 HTTP {r.status_code}")
                clients[level] = c
            return clients[level]
        for i, (c, level) in enumerate(todo, 1):
            u = c["users"][level]
            user_id = u.get("user_id") or level_user[level]
            t0 = time.perf_counter()
            row = {"key": answer_key(c["qid"], level), "qid": c["qid"], "level": level, "user_id": user_id, "config": cfg,
                   "path": "api"}
            guard.context = f"{cfg}/{c['qid']}/{level}"
            failed0, err = guard.stats["failed"], None
            probe.take()
            spy.take()
            try:
                r = client(level, user_id).post("/api/chat", json={"query": c["query"], "restore": False})
                body = r.json() if r.content else {}
                cap = spy.take()
                res = cap.get("res") or {}
                row.update(http=r.status_code, api_body=body,      # 실제 API 응답 전체 → X04·X06 은 이것으로 채점
                           request_id=body.get("request_id"), masked_query=body.get("masked_query"),
                           query_tokens=body.get("query_tokens") or [], meta=cap.get("meta"),
                           probe=probe.take(body.get("masked_query")), llm_input_doc_ids=cap.get("llm_input_doc_ids"),
                           ui_state=body.get("state"), answer=body.get("answer"))
                if guard.stats["failed"] > failed0:               # API 안에서 LLM 호출이 끝내 실패 → 실행 오류
                    err = guard.last_error
                    row["exec_error"] = err.info() if err else {"kind": "llm", "message": f"HTTP {r.status_code}"}
                elif r.status_code != 200:                       # 저장소 코드의 예외 → 챗봇 결함으로 채점
                    row.update(state="error", error=str(body.get("detail"))[:200])
                else:
                    row.update(state=res.get("state"), notice=res.get("notice"), recheck=res.get("recheck"),
                               docs=[{"doc_id": d["doc_id"], "tokens": d.get("tokens") or [],
                                      "masked_text": d.get("masked_text"), "snippet": d.get("snippet")}
                                     for d in res.get("docs") or []])
                if row.get("request_id") and "exec_error" not in row:
                    row["audit"] = audit_check(row["request_id"], user_id, level, body.get("state"),
                                               [d.get("doc_id") for d in body.get("docs") or []], raw_values, _ui_state)
            except Exception as exc:
                err = exc
                if is_exec_error(exc):
                    row["exec_error"] = exec_error_info(exc)
                else:
                    row.update(state="error", error=f"{type(exc).__name__}: {str(exc)[:150]}")
            row["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            jl_write(ap, [row], "a")                            # 요청마다 바로 저장
            if row.get("exec_error"):
                breaker.fail(err or RuntimeError(str(row["exec_error"])), f"{cfg}/{c['qid']}/{level} 답변")
            else:
                breaker.ok()
            if i % 10 == 0:
                print(f"  답변 {i}/{total}  (LLM 호출 {guard.stats['calls']}, 재시도 {guard.stats['retries']}, "
                      f"실패 {guard.stats['failed']})", flush=True)


def dump_masked(run_dir, phase):
    """통합 테스트 84건의 실제 가명처리 본문(rag.chunks.masked_text)을 남긴다 → 원문 잔존·토큰 검사용."""
    from starter import db
    with db.rag_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT doc_id, chunk_idx, masked_text, tokens FROM rag.chunks WHERE doc_id LIKE 'IT%%' "
                    "ORDER BY doc_id, chunk_idx")
        rows = [{"doc_id": d, "chunk_idx": i, "masked_text": m, "tokens": list(t or [])} for d, i, m, t in cur.fetchall()]
    jl_write(run_dir / f"masked_{phase}.jsonl", rows)
    print(f"  가명처리 본문 {len(rows)}청크 저장 (masked_{phase}.jsonl)")


def dump_person_rows(run_dir):
    from starter import db
    from psycopg.rows import dict_row
    with db.vault_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT person_id, reg_id, token, role, info_label, evidence, doc_id, resolution_status FROM vault.person_info")
        rows = cur.fetchall()
    for r in rows:
        r["doc_id"] = r["doc_id"][0]
    (run_dir / "person_rows.json").write_text(json.dumps(rows, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"  인물사전 판정 {len(rows)}행 저장")


def _load_manifest(run_dir):
    p = run_dir / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _check_resume(prev, manifest, configs, allow):
    """이어서 실행할 때 이전 실행과 조건이 같은지 확인. 다르면 섞인 결과가 되므로 멈춘다."""
    problems = []
    if prev.get("data_manifest") != manifest["data_manifest"]:
        problems.append("테스트 데이터(it_manifest 해시)가 이전 실행과 다릅니다")
    ph, mh = prev.get("holdout") or {}, manifest["holdout"]
    if ph and (ph.get("ids_sha") != mh["ids_sha"] or ph.get("docs") != mh["docs"]
               or (ph.get("content_sha") and ph["content_sha"] != mh["content_sha"])):
        problems.append(f"함께 적재하는 기존 문서가 다릅니다 (이전 {ph.get('path')} {ph.get('docs')}건, 지금 {mh['path']} {mh['docs']}건)")
    for c in configs:
        if c in prev.get("configs", {}) and prev["configs"][c] != manifest["configs"][c]:
            problems.append(f"{c} 설정 환경값이 이전 실행과 다릅니다")
    for k, v in manifest["runtime"].items():
        if k in (prev.get("runtime") or {}) and prev["runtime"][k] != v:
            problems.append(f"실행 환경 {k} 가 다릅니다 (이전 {prev['runtime'][k]}, 지금 {v})")
    if prev.get("commit") and manifest["commit"] and prev["commit"] != manifest["commit"]:
        problems.append(f"코드 커밋이 다릅니다 (이전 {prev['commit']}, 지금 {manifest['commit']})")
    if prev.get("dirty") != manifest["dirty"] and manifest["dirty"]:
        problems.append("커밋되지 않은 코드 변경이 있습니다 (git status)")
    if problems and not allow:
        raise SystemExit("이어서 실행할 수 없습니다:\n  - " + "\n  - ".join(problems)
                         + "\n새 --run-id 로 다시 실행하거나, 알고 있는 차이라면 --allow-mismatch 를 붙이세요.")
    for x in problems:
        print(f"  ! {x} (--allow-mismatch)")


def _expected_db(st, phase, holdout):
    """적재가 '기존 문서 + 통합 테스트 84건, 인물사전 phase' 인지."""
    problems = []
    if st["phase"] != phase:
        problems.append(f"인물사전 {st['phase'].upper()} 상태")
    if st["it_docs"] != 84:
        problems.append(f"통합 테스트 문서 {st['it_docs']}건 (84건이어야 함)")
    if st["other_docs"] != holdout["docs"]:
        problems.append(f"기존 문서 {st['other_docs']}건 (홀드아웃 {holdout['docs']}건이어야 함)")
    return problems


def _same_db(st, ref):
    keys = ("docs", "doc_hash", "chunks", "look_up", "phase")
    return ref is None or all(st.get(k) == ref.get(k) for k in keys if k in ref)


def _runtime():
    """재현에 필요한 실행 환경 (값만, 키·비밀번호 제외)."""
    from starter import config
    keys = ("LLM_MODEL", "LLM_API_BASE", "LLM_MAX_TOKENS", "LLM_TEMPERATURE", "EMBED_PROVIDER", "EMBED_MODEL", "EMBED_DIM")
    out = {}
    for k in keys:
        v = getattr(config, k, None)
        if v is None:
            v = os.environ.get(k)
        if v is not None:
            out[k] = str(v)
    return out


def _merge_plan(old, new):
    out = {"search": {}, "answer": {}, "dialog": {},
           "security": sorted(set((old or {}).get("security", [])) | set(new["security"]))}
    for area in ("search", "answer", "dialog"):
        cfgs = set((old or {}).get(area, {})) | set(new[area])
        for c in cfgs:
            out[area][c] = sorted(set((old or {}).get(area, {}).get(c, [])) | set(new.get(area, {}).get(c, [])))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--configs", default=",".join(DEFAULT_CONFIGS), help="쉼표로 구분 (R0,R1,R2,R3,R2c,'R2 + REGION_META')")
    ap.add_argument("--run-id", default=dt.datetime.now().strftime("%Y%m%d_%H%M"))
    ap.add_argument("--resume", action="store_true",
                    help="같은 실행 ID를 이어서: 정상 실행된 질문은 건너뛰고 실행 오류·미실행 질문만 실행")
    ap.add_argument("--allow-mismatch", action="store_true", help="이어서 실행할 때 설정·데이터·커밋·DB 차이를 알고도 진행")
    ap.add_argument("--rebuild-db", action="store_true",
                    help="DB를 지우고 기존 문서 + 통합 테스트 84건을 설정에 맞게 다시 적재 (docker compose down -v). "
                         "--resume 과 함께 쓰면 DB가 맞지 않을 때만")
    ap.add_argument("--holdout", default=str(ROOT / "data" / "holdout" / "holdout_labeled.jsonl"),
                    help="함께 적재할 기존 문서 JSONL (기본: 적재 코드 기본값 data/holdout/holdout_labeled.jsonl)")
    ap.add_argument("--yes", action="store_true", help="--rebuild-db 확인 질문 생략")
    ap.add_argument("--compose-project", default=os.environ.get("IT_COMPOSE_PROJECT", "pii_starter"),
                    help="--rebuild-db 로 지울 Docker 프로젝트 (기본 IT_COMPOSE_PROJECT 또는 pii_starter)")
    ap.add_argument("--wipe-other-data", action="store_true",
                    help="지울 DB에 테스트와 관계없는 문서가 있어도 --rebuild-db 를 진행")
    ap.add_argument("--only", choices=["all", "search", "answer", "dialog", "security"], default="all",
                    help="search: 검색 평가만(LLM 없음), answer: 답변 평가만, dialog: 다중 턴 대화만(R1·R3), "
                         "security: 권한 API만")
    ap.add_argument("--api-config", default="R2",
                    help="권한 API 케이스를 돌릴 실행 설정 (기본 R2 = 현재 시스템). 엑셀 04 시트 E6 'API 평가 설정'과 같게")
    ap.add_argument("--split", choices=["dev", "check", "all"], default="all")
    ap.add_argument("--limit", type=int, help="질문 수 제한 (빠른 점검용)")
    ap.add_argument("--skip-answers", action="store_true", help="LLM 답변·대화 실행 생략 (검색만)")
    ap.add_argument("--skip-security", action="store_true")
    ap.add_argument("--prepare-data", action="store_true", help="원본(it_source)에서 파생 JSONL을 다시 만들고 검사한 뒤 실행")
    ap.add_argument("--skip-validate", action="store_true", help="--prepare-data 때 정합성 검사 생략")
    ap.add_argument("--stop-after", type=int, default=3, help="실행 오류가 이 횟수만큼 연속되면 저장하고 멈춤 (기본 3)")
    a = ap.parse_args()

    do_search = a.only in ("all", "search")
    do_answer = a.only in ("all", "answer") and not a.skip_answers
    do_security = a.only in ("all", "security") and not a.skip_security
    do_dialog = a.only in ("all", "dialog") and not a.skip_answers
    do_questions = do_search or do_answer or do_dialog

    configs = [c.strip() for c in a.configs.split(",") if c.strip()]
    for c in configs:
        if c in UNAVAILABLE:
            print(f"! {c} 건너뜀: {UNAVAILABLE[c]}")
        elif c not in CONFIGS:
            raise SystemExit(f"알 수 없는 설정: {c}")
    configs = [c for c in configs if c in CONFIGS]
    run_dir = OUT / a.run_id
    prev = _load_manifest(run_dir)
    if not a.resume and (prev or (run_dir / "traces").exists() or (run_dir / "security.jsonl").exists()):
        raise SystemExit(f"이미 있는 실행 ID입니다: {run_dir}\n이어서 하려면 --resume, 새로 하려면 다른 --run-id 를 주세요.")
    if a.resume and not prev:
        print(f"  (참고) {run_dir} 에 이전 실행 기록이 없어 처음부터 실행합니다")
    if not pathlib.Path(a.holdout).exists():
        raise SystemExit(f"함께 적재할 기존 문서 파일이 없습니다: {a.holdout}\n--holdout 으로 경로를 지정하세요.")
    holdout = holdout_info(a.holdout)
    if holdout["it_overlap"]:
        raise SystemExit(f"기존 문서에 통합 테스트 문서와 겹칠 수 있는 ID가 있습니다: {holdout['it_overlap']}")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"실행 ID {a.run_id} → {run_dir}  (실행 범위: {a.only}, 기존 문서 {holdout['docs']}건 + 통합 테스트 84건)")

    if a.rebuild_db and not a.yes:
        what = "DB가 이전 실행과 맞지 않을 때만 " if a.resume else ""
        ans = input(f"{what}Docker 프로젝트 '{a.compose_project}' 의 DB(적재 데이터·감사 로그)를 모두 지우고 다시 적재합니다. "
                    f"계속하려면 yes: ")
        if ans.strip().lower() != "yes":
            raise SystemExit("취소했습니다.")

    if a.prepare_data:
        prepare(a.skip_validate)
    data_sha = check_data()
    cases = jl_read(DATA / "it_cases.jsonl")
    if a.split != "all":
        cases = [c for c in cases if c["split"] == a.split]
    if a.limit:
        cases = cases[: a.limit]
    dialogs = jl_read(DATA / "it_dialogs.jsonl") if do_dialog else []
    if dialogs and a.split != "all":
        from .score_dialog import dialog_split
        dialogs = [d for d in dialogs if dialog_split(d) == a.split]

    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        except Exception:
            return ""
    now = dt.datetime.now().isoformat(timespec="seconds")
    manifest = {"run_id": a.run_id, "started": now, "commit": git("rev-parse", "--short", "HEAD"),
                "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
                "configs": {c: config_env(c) for c in configs}, "questions": len(cases), "split": a.split,
                "data_manifest": data_sha,
                "holdout": {k: v for k, v in holdout.items() if k != "ids"}, "runtime": _runtime(), "phases": {}, "skipped": {}, "sessions": [], "plan": None}
    # 실행 대상 목록: 이번 실행에서 돌리기로 한 설정 × 영역 × 질문 (결과 파일이 없어도 '미실행'으로 센다)
    access_ids = [c["id"] for c in jl_read(DATA / "it_access_cases.jsonl")]
    plan = {"search": {c: [x["qid"] for x in cases if c in x["search_configs"]] for c in configs} if do_search else {},
            "answer": {c: [x["qid"] for x in cases if c in x["answer_configs"]] for c in configs} if do_answer else {},
            "dialog": {c: [d["id"] for d in dialogs] for c in configs if c in DIALOG_CONFIGS} if do_dialog else {},
            "security": access_ids if do_security else []}
    if prev:
        _check_resume(prev, manifest, configs, a.allow_mismatch)
        merged = dict(prev)
        merged["configs"] = {**prev.get("configs", {}), **manifest["configs"]}
        merged.setdefault("phases", {})
        merged.setdefault("sessions", [])
        merged["skipped"] = {}
        merged["questions"] = max(prev.get("questions", 0), len(cases))
        merged.setdefault("holdout", manifest["holdout"])
        merged.setdefault("runtime", manifest["runtime"])
        manifest = merged
    manifest["plan"] = _merge_plan(manifest.get("plan"), plan)
    guard = LLMGuard(run_dir).install()
    session = {"started": now, "only": a.only, "configs": configs, "split": a.split, "limit": a.limit,
               "qids": [c["qid"] for c in cases], "resume": bool(prev), "llm_settings": guard.settings(),
               "note": "LLM 호출은 평가용 호출 관리(속도 제한·재시도)로 바꿔 끼워 실행 (저장소 chat._post_llm 대신)"}
    manifest["sessions"].append(session)
    breaker = Breaker(max(a.stop_after, 1))

    def save(status, reason=""):
        session.update(status=status, reason=reason, ended=dt.datetime.now().isoformat(timespec="seconds"),
                       llm_stats=guard.stats)
        manifest["status"] = status
        (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    save("running")

    def stop(status, why, code):
        save(status, why)
        print(f"\n■ 실행을 멈췄습니다: {why}")
        print(f"  지금까지의 결과는 저장했습니다. LLM 오류 기록: {run_dir / 'llm_calls.jsonl'}")
        from . import report
        try:
            report.build(run_dir)
        except Exception as exc:                          # 부분 보고서가 실패해도 기록은 남아 있다
            print(f"  (부분 보고서 생성 실패: {type(exc).__name__}: {exc})")
        print(f"\n이어서 실행하세요 (정상 실행된 질문은 다시 부르지 않습니다):\n"
              f"  python -m eval.integration.run --run-id {a.run_id} --resume --configs {','.join(configs)}"
              + (f" --only {a.only}" if a.only != "all" else "") + (f" --holdout {a.holdout}" if a.holdout != ap.get_default("holdout") else ""))
        sys.exit(code)

    try:
        # 지금 DB 상태와 같은 적재부터 실행해 재적재를 줄이고, 남은 일이 없는 적재는 재적재·실행을 생략한다
        try:
            cur_phase = db_state()["phase"]
        except Exception:
            cur_phase = None
        phases = ("on", "off") if cur_phase == "on" else ("off", "on")
        if do_security and a.api_config in CONFIGS:      # 권한 API 설정의 적재를 마지막에 두어 재적재 없이 이어서 실행
            api_phase = PHASE[a.api_config]
            phases = tuple(p for p in phases if p != api_phase) + (api_phase,)
        for phase in phases if do_questions else ():
            group = [c for c in configs if PHASE[c] == phase and (do_search or do_answer or c in DIALOG_CONFIGS)]
            if not group:
                continue
            todo = phase_todo(group, cases, run_dir, do_search, do_answer, dialogs)
            extra = (not (run_dir / f"masked_{phase}.jsonl").exists()
                     or (phase == "on" and not (run_dir / "person_rows.json").exists()))
            if todo == 0 and not extra:
                print(f"\n[적재] 인물사전 {phase.upper()} ({', '.join(group)}): 남은 질문이 없어 적재·실행을 생략합니다")
                continue
            ref = manifest["phases"].get(phase)          # 이전 실행에서 기록한 이 적재의 DB 상태
            try:
                st = db_state()
            except Exception:
                st = None
            need = st is None or bool(_expected_db(st, phase, holdout)) or not _same_db(st, ref)
            if a.rebuild_db and (not a.resume or need):
                rebuild_db(phase, a.compose_project, run_dir, holdout, a.wipe_other_data)
            elif a.rebuild_db:
                print(f"\n[적재] 인물사전 {phase.upper()} DB가 이전 실행과 같아 다시 적재하지 않습니다")
            elif need:
                left = f"질문 {todo}개" if todo else "가명처리 본문·인물 판정 수집"
                print(f"\n[적재] 인물사전 {phase.upper()} ({', '.join(group)}): {left}이(가) 남았지만 DB가 이 적재와 다릅니다."
                      f" 이어서 하려면 --rebuild-db 를 붙이세요 (DB가 맞지 않는 적재만 다시 합니다).")
            try:
                st = db_state()
            except Exception as exc:
                raise SystemExit(f"DB에 연결할 수 없습니다 ({type(exc).__name__}). Docker DB가 켜져 있는지 확인하세요:\n"
                                 f"  docker compose -p {a.compose_project} --env-file .env -f infra/compose.yaml up -d --wait")
            print(f"\n[DB] 문서 {st['docs']}건 (기존 {st['other_docs']} + 통합 테스트 {st['it_docs']}), "
                  f"청크 {st['chunks']}, 인물사전 {st['phase'].upper()} (검색 연결 {st['look_up']})")
            problems = _expected_db(st, phase, holdout)
            if problems:
                why = "DB가 설계와 다름: " + ", ".join(problems)
                print(f"  ! {', '.join(group)} 건너뜀: {why}. --rebuild-db 로 다시 적재하세요.")
                manifest["skipped"].update({c: why for c in group})
                continue
            if not _same_db(st, ref):
                why = (f"DB가 이전 실행과 다름 (문서 ID 해시·청크·연결 수: 이전 {ref.get('doc_hash', '')[:8]}·{ref.get('chunks')}·"
                       f"{ref.get('look_up')}, 지금 {st['doc_hash'][:8]}·{st['chunks']}·{st['look_up']})")
                if not a.allow_mismatch:
                    print(f"  ! {', '.join(group)} 건너뜀: {why}. --rebuild-db 를 붙이면 다시 적재합니다.")
                    manifest["skipped"].update({c: why for c in group})
                    continue
                print(f"  ! {why} (--allow-mismatch)")
            if not (run_dir / f"pseudo_{phase}.jsonl").exists():
                src = ROOT / "outputs" / "pseudo_it_docs.jsonl"
                if src.exists() and ref is None:
                    print(f"  (참고) 이번 실행에서 적재하지 않아 {src.name} 을 탐지 채점에 씁니다 (같은 적재인지 확인하세요)")
                    shutil.copy(src, run_dir / f"pseudo_{phase}.jsonl")
            manifest["phases"][phase] = st
            if not (run_dir / f"masked_{phase}.jsonl").exists():
                dump_masked(run_dir, phase)
            save("running")
            for cfg in group:
                if do_search or do_answer:
                    run_questions(cfg, cases, run_dir, bool(prev), do_search, do_answer, guard, breaker)
                if dialogs and cfg in DIALOG_CONFIGS:
                    run_dialogs_api(cfg, run_dir, dialogs, guard, breaker)
            if phase == "on" and not (run_dir / "person_rows.json").exists():
                dump_person_rows(run_dir)
            save("running")

        if do_security:
            from . import security
            if a.api_config not in CONFIGS:
                raise SystemExit(f"알 수 없는 --api-config: {a.api_config}")
            apply_env(config_env(a.api_config))            # 권한 API 는 이 설정으로 (엑셀 E6 와 같게)
            manifest["api_config"] = a.api_config
            api_phase = PHASE[a.api_config]
            try:
                st = db_state()
            except Exception:
                st = None
            if a.rebuild_db and (st is None or _expected_db(st, api_phase, holdout)):
                rebuild_db(api_phase, a.compose_project, run_dir, holdout, a.wipe_other_data, keep_dumps=True)
                st = db_state()
            problems = ["DB 연결 실패"] if st is None else _expected_db(st, api_phase, holdout)
            if problems:                                  # 설정과 DB 적재가 다르면 권한 API 를 돌리지 않는다 (미실행으로 남음)
                why = (f"권한 API 설정 {a.api_config}(인물사전 {api_phase.upper()})와 DB가 다름: " + ", ".join(problems))
                print(f"\n  ! 권한 API 건너뜀: {why}. --rebuild-db 를 붙이거나 맞는 --api-config 로 다시 실행하세요.")
                manifest["skipped"]["API"] = why
            else:
                manifest["api_db"] = st
                save("running")
                security.run(run_dir, resume=bool(prev), breaker=breaker)
    except StopRun as exc:
        stop("stopped", str(exc), 2)
    except KeyboardInterrupt:
        stop("interrupted", "사용자가 중단함 (Ctrl+C)", 130)
    finally:
        guard.uninstall()
    manifest["finished"] = dt.datetime.now().isoformat(timespec="seconds")
    save("completed")

    from . import report
    report.build(run_dir)


if __name__ == "__main__":
    main()
