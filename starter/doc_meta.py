"""문서 메타데이터: 문서별 담당 팀·담당 직원과 직원 목록을 만든다 (종합 과제, 2026-11-26).

    python -m starter.doc_meta                  # rag.chunks 를 읽어 rag.doc_meta 갱신 + outputs/ 에 jsonl 저장
    python -m starter.doc_meta --no-db-write    # 계산·파일 저장만 (DB 쓰기 없음)
    python -m starter.doc_meta --from-data data/dev/pii_synth_finance_dev.jsonl data/test/pii_synth_finance_test.jsonl \\
        --gold data/doc_meta_gold.csv
        # DB 없이 원문 파일을 탐지·가명처리해 판정 (채점용, vault 쓰기 없음). 적재하지 않은 dev 문서 채점에 쓴다.

입력은 가명처리된 본문(rag.chunks.masked_text)뿐이다. 팀명, "담당", "상담사" 같은 단서는 개인정보가 아니라
가명처리 후에도 남아 있으므로 원문이 필요 없다. 담당 직원은 처음부터 PERSON 토큰으로 나온다.
정답 파일(entities, persons_gold)은 쓰지 않는다.

원칙: 적게 정하더라도 정한 것은 맞게. 근거가 확실하지 않으면 정하지 않고(NULL) status 를 restricted 로 둔다.
권한에 영향을 주는 것은 담당 팀(teams)뿐이며, 그것도 내부업무 Lv1 문서에서만 쓰인다(권한 표는 중간 정리 문서 참고).

담당 팀 근거 (같은 문서 안에서만. 직원 목록 역추적은 후속 과제)
  T1 소속 항목      "부서:", "소속:", "부서/직급:" 뒤의 팀명
  T2 서명·발신·문의  마지막 3줄의 팀 서명("새봄카드 ○○팀", "○○팀 … 드림", "Digital Strategy Team"),
                    "작성: ○○팀", "보낸사람: ○○팀", "문의: ○○팀"
  T3 소개 줄        "○○팀 <PERSON>입니다"
  T4 담당 직원 옆   담당 직원 토큰과 같은 줄, 앞뒤 2어절(표의 행이면 그 행) 안의 팀명
  T5 소속 표        "성명 | 소속" 같은 표(머리글에 '소속')의 행에 적힌 팀들 (출장보고 등. 여러 팀이면 모두)
  T6 머리 제목      첫 줄이 "[분류] ○○팀 …" 형태일 때의 팀명 (예: "[주간 회의] 고객보호팀 주간 업무 점검")
  T1~T3 끼리, 또는 T1~T3 와 T4 가 다르면 충돌 → restricted.
  채용메일은 담당 팀이 항상 인사팀(문서 유형 규칙, F1).

담당 직원 (우선순위가 높은 근거에서 처음 찾은 사람. 같은 순위에 서로 다른 사람이 둘 이상이면 정하지 않음)
  O1 담당 항목  담당자·담당·작성자·처리자·접수 담당·신청자·소명자·사용자·상담사·상담원·심사역·확인 담당·검토자 등,
                계약서의 "갑: ○○팀 <PERSON>" 줄 (우리 회사 쪽 담당자. 팀명이 바로 붙은 경우만)
  O2 작성·발신  "○○팀 <PERSON>입니다", 마지막 3줄의 "<PERSON> 드림"·"Best regards" 아래 이름,
                "○○팀 <PERSON>(…)에게 연락/문의" (연락 담당)
      고객업무 문서의 O2 는 이름 옆에 팀·직급 단서가 있을 때만 쓴다(고객이 쓴 편지 제외).
  제외: 고객으로 나오는 사람, 이름만 쓴 표기(정규화 이름 2자 이하), 승인·확인자·참조·면접위원 등(항목 목록에 없음),
        외부 쪽 항목("수탁사 담당자", "협력사 담당", "피신청인 … 담당" 등 같은 줄 앞에 외부 표시가 있는 경우)
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

try:
    from . import config
except ImportError:                      # 단독 실행
    config = None

PERSON = r"<PERSON_[0-9a-f]+>"
PERSON_RE = re.compile(PERSON)

# 팀 사전 별칭 (policy.teams 의 이름 외 표기). 영문 서명 등
TEAM_ALIASES = {"Digital Strategy Team": "DEPT_DIGITAL"}

POSITIONS = ("팀장", "실장", "센터장", "본부장", "지점장", "부서장")
GRADES = ("사원", "주임", "대리", "과장", "차장", "부장", "선임", "책임", "수석", "매니저", "심사역")

# O1 담당 항목 (긴 것부터 맞춘다). 승인·확인자·참조·면접위원·고객 쪽 항목(신청인·고객명 등)은 넣지 않는다
OWNER_LABELS = sorted([
    "담당자", "담당", "담당 심사역", "심사역", "작성자", "처리자", "처리 담당", "처리", "접수 담당", "접수자",
    "신청자 성명", "신청자", "요청자", "소명자", "사용자", "상담사", "상담원", "확인 담당", "검토자",
    "보낸사람", "보낸 사람", "문의",
], key=len, reverse=True)
EXTERNAL_CUES = ("수탁사", "협력사", "피신청인", "업체", "거래처", "위탁사", "용역사", "외부")
TEAM_FIELD_LABELS = ("부서/직급", "담당 부서", "부서", "소속")
CUSTOMER_CUES = ("고객", "회원", "민원인", "신청인", "피해 고객", "대상 고객", "위임인", "대리인", "송금인",
                 "수취인", "예금주", "대표자", "가입자", "명의자", "지원자")


def load_teams():
    """policy/access_policy.yaml 의 팀 목록 + 별칭 → [(표기, team_cd)] (긴 표기부터)."""
    from policy.access_policy import load_policy
    teams = load_policy().teams
    pairs = [(name, cd) for cd, name in teams.items()] + list(TEAM_ALIASES.items())
    return sorted(pairs, key=lambda p: len(p[0]), reverse=True)


class DocMetaBuilder:
    def __init__(self, teams=None, names=None, categories=None):
        """teams: [(표기, team_cd)], names: {PERSON 토큰: 정규화 이름} (없으면 이름 길이 검사 생략),
        categories: {doc_type: 분류} (없으면 policy yaml)."""
        self.teams = teams or load_teams()
        self.names = names or {}
        if categories is None:
            from policy.access_policy import load_policy
            categories = load_policy().categories
        self.categories = categories
        alt = "|".join(re.escape(t) for t, _ in self.teams)
        self.team_re = re.compile(f"({alt})")
        self.team_cd = dict(self.teams)
        lab = "|".join(re.escape(l) for l in OWNER_LABELS)
        # 항목명 [:：]? (팀·직급 등 0~3어절) 토큰.  예: "처리: 고객보호팀 강혁", "담당 심사역 양서현", "상담사 지아:"
        self.owner_re = re.compile(rf"(?<![가-힣])({lab})\s*[:：]?\s*((?:[^\s<:：|]+\s*[/,]?\s*){{0,3}}?)({PERSON})")
        self.field_re = re.compile(rf"(?:{'|'.join(map(re.escape, TEAM_FIELD_LABELS))})\s*[:：]\s*({alt})")
        self.intro_re = re.compile(rf"({alt})\s*({PERSON})\s*입니다")
        self.contact_re = re.compile(rf"({alt})\s*({PERSON})\s*(?:\([^)]*\))?\s*에게\s*(?:연락|문의)")
        self.title_re = re.compile(rf"^\s*\[[^\]]+\]\s*({alt})")
        # 계약서 담당자 조항의 우리 쪽 줄: "갑: IT운영팀 <PERSON> (…)". 팀명이 바로 붙은 경우만 (대표이사 서명 줄 제외)
        self.party_re = re.compile(rf"^\s*갑\s*[:：]\s*({alt})\s*({PERSON})")

    # ---- 팀 ----------------------------------------------------------------
    def _teams_in(self, s):
        return [self.team_cd[m] for m in self.team_re.findall(s)]

    def _near_team(self, line, start, end):
        """토큰 [start,end) 앞뒤 2어절(표의 행이면 행 전체) 안의 팀. 하나면 그 팀, 아니면 None."""
        if line.lstrip().startswith("|"):
            found = set(self._teams_in(line))
        else:
            words = line[:start].split()[-2:] + line[end:].split()[:2]
            found = set(self._teams_in(" ".join(words)))
        return found.pop() if len(found) == 1 else None

    def doc_team_evidence(self, lines):
        ev = []                                   # (rule, team_cd)
        for ln in lines:
            for m in self.field_re.finditer(ln):
                ev.append(("T1", self.team_cd[m.group(1)]))
            for m in self.intro_re.finditer(ln):
                ev.append(("T3", self.team_cd[m.group(1)]))
            for lab in ("작성", "보낸사람", "보낸 사람", "문의", "교육 문의"):
                for m in re.finditer(rf"(?<![가-힣]){lab}\s*[:：]\s*({self.team_re.pattern})", ln):
                    ev.append(("T2", self.team_cd[m.group(1)]))
        first = next((l for l in lines if l.strip()), "")
        m = self.title_re.match(first)
        if m:
            ev.append(("T6", self.team_cd[m.group(1)]))
        in_table = False                          # T5: 머리글에 '소속'이 있는 표의 행
        for ln in lines:
            if ln.lstrip().startswith("|"):
                if "소속" in ln:
                    in_table = True
                elif in_table:
                    for t in self._teams_in(ln):
                        ev.append(("T5", t))
            else:
                in_table = False
        tail = [l.strip() for l in lines if l.strip()][-3:]
        for ln in tail:                           # 서명 줄: 팀명 위주의 짧은 줄
            rest = PERSON_RE.sub("", ln)
            teams = self._teams_in(rest)
            if len(set(teams)) != 1:
                continue
            leftover = self.team_re.sub("", rest)
            leftover = re.sub(r"새봄카드|Saebom Card|드림|일동|올림|[,()\s]", "", leftover)
            if len(leftover) <= 4:                # 팀명 외 거의 없음 (이름 토큰·직급 정도)
                ev.append(("T2", teams[0]))
        return ev

    # ---- 담당 직원 ---------------------------------------------------------
    def _is_customer(self, tok, text):
        for m in re.finditer(re.escape(tok), text):
            before, after = text[max(0, m.start() - 8):m.start()], text[m.end():m.end() + 6]
            if any(before.rstrip(" :：").endswith(c) for c in CUSTOMER_CUES) or after.lstrip().startswith(("고객", "님 귀하")):
                return True
        return False

    @staticmethod
    def _external(line, start):
        """같은 줄의 토큰 앞부분에 외부 표시(수탁사·협력사·피신청인 등)가 있으면 외부 쪽 사람."""
        return any(c in line[:start] for c in EXTERNAL_CUES)

    def _given_only(self, tok):
        n = self.names.get(tok)
        return n is not None and len(re.sub(r"\s+", "", n)) <= 2

    def owner_candidates(self, lines, category):
        cands = []                                # (priority, token, line_idx, start, end)
        for i, ln in enumerate(lines):
            for m in self.owner_re.finditer(ln):
                cands.append((1, m.group(3), i, m.start(3), m.end(3)))
            for m in self.intro_re.finditer(ln):
                cands.append((2, m.group(2), i, m.start(2), m.end(2)))
            for m in self.contact_re.finditer(ln):
                cands.append((2, m.group(2), i, m.start(2), m.end(2)))
            m = self.party_re.match(ln)
            if m:
                cands.append((1, m.group(2), i, m.start(2), m.end(2)))
        nonempty = [i for i, l in enumerate(lines) if l.strip()]
        tail = nonempty[-3:]
        for j, i in enumerate(tail):
            ln = lines[i]
            for m in re.finditer(rf"({PERSON})\s*(?:{'|'.join(GRADES + POSITIONS)})?\s*(?:님)?\s*드림", ln):
                cands.append((2, m.group(1), i, m.start(1), m.end(1)))
            if j > 0 and re.search(r"(Best regards|Regards|Sincerely)", lines[tail[j - 1]]):
                m = re.match(rf"\s*({PERSON})\s*$", ln)
                if m:
                    cands.append((2, m.group(1), i, m.start(1), m.end(1)))
        if category == "고객업무":                # 고객이 쓴 문서의 서명은 담당자가 아님
            cands = [c for c in cands if c[0] == 1 or self._has_staff_cue(lines[c[2]], c[3], c[4])]
        return cands

    def _has_staff_cue(self, line, start, end):
        words = line[:start].split()[-2:] + line[end:].split()[:2]
        s = " ".join(words)
        return bool(self._teams_in(s)) or any(g in s for g in GRADES + POSITIONS)

    def pick_owner(self, lines, text, category):
        cands = self.owner_candidates(lines, category)
        cands = [c for c in cands if not self._is_customer(c[1], text) and not self._given_only(c[1])
                 and not self._external(lines[c[2]], c[3])]
        if not cands:
            return None, None, "O-none"
        best = min(c[0] for c in cands)
        top = [c for c in cands if c[0] == best]
        if len({c[1] for c in top}) > 1:
            return None, None, "O-tie"
        c = top[0]
        return c[1], self._near_team(lines[c[2]], c[3], c[4]), f"O{best}"

    # ---- 문서 1건 ----------------------------------------------------------
    def build_doc(self, doc_id, doc_type, text):
        lines = text.split("\n")
        category = self.categories.get(doc_type)
        owner, owner_team, orule = self.pick_owner(lines, text, category)
        if doc_type == "채용메일":
            teams, trule = ["DEPT_HR"], "F1"
        else:
            ev = self.doc_team_evidence(lines)
            trule = "+".join(sorted({r for r, _ in ev})) or "T-none"
            single = sorted({t for r, t in ev if r != "T5"})     # 문서 하나의 팀을 가리키는 근거
            table = sorted({t for r, t in ev if r == "T5"})      # 참석자 표: 여러 팀이 함께 담당
            if single:
                doc_teams = single if not table or set(table) >= set(single) else single + ["CONFLICT"]
            else:
                doc_teams = table
            if (len(single) > 1 or "CONFLICT" in doc_teams
                    or (doc_teams and owner_team and owner_team not in doc_teams)):
                return self._row(doc_id, doc_type, [], owner, "restricted", f"conflict({trule},{orule})")
            if doc_teams:
                teams = doc_teams
            elif owner_team:
                teams, trule = [owner_team], "T4"
            else:
                teams = []
        status = "restricted" if not teams else ("resolved" if owner else "team_only")
        return self._row(doc_id, doc_type, teams, owner, status, f"{trule},{orule}")

    @staticmethod
    def _row(doc_id, doc_type, teams, owner, status, rule):
        return {"doc_id": doc_id, "doc_type": doc_type, "teams": teams, "owner_token": owner,
                "status": status, "rule_id": rule}

    # ---- 직원 목록 ---------------------------------------------------------
    def employee_mentions(self, doc_id, text):
        """이름 토큰 옆(앞뒤 2어절, 표의 행)에 팀명이 있는 언급 → (token, team_cd, position, grade)."""
        out = []
        for ln in text.split("\n"):
            for m in PERSON_RE.finditer(ln):
                team = self._near_team(ln, m.start(), m.end())
                if not team or self._given_only(m.group()):
                    continue
                words = " ".join(ln[:m.start()].split()[-2:] + ln[m.end():].split()[:2])
                pos = next((p for p in POSITIONS if p in words), None)
                grade = next((g for g in GRADES if g in words), None)
                out.append((m.group(), team, pos, grade))
        return out


def build_all(docs, builder=None):
    """docs: [{doc_id, doc_type, text(가명처리 본문)}] (doc_id 순서대로) → (doc_meta rows, employees rows)."""
    b = builder or DocMetaBuilder()
    emp = {}                                      # token -> dict
    for d in docs:
        for tok, team, pos, grade in b.employee_mentions(d["doc_id"], d["text"]):
            e = emp.setdefault(tok, {"person_token": tok, "teams": set(), "position": None, "grade": None,
                                     "doc_ids": []})
            e["teams"].add(team)
            e["position"] = e["position"] or pos
            e["grade"] = e["grade"] or grade
            if d["doc_id"] not in e["doc_ids"]:
                e["doc_ids"].append(d["doc_id"])
    metas = [b.build_doc(d["doc_id"], d["doc_type"], d["text"]) for d in docs]
    for m in metas:                               # 담당 직원은 팀을 몰라도 목록에 올린다
        if m["owner_token"] and m["owner_token"] not in emp:
            emp[m["owner_token"]] = {"person_token": m["owner_token"], "teams": set(), "position": None,
                                     "grade": None, "doc_ids": [m["doc_id"]]}
    employees, ids = [], {}
    for i, e in enumerate(emp.values(), 1):       # 처음 나온 순서대로 E001… (같은 입력이면 같은 ID)
        teams = sorted(e["teams"])
        ids[e["person_token"]] = f"E{i:03d}"
        employees.append({"emp_id": f"E{i:03d}", "person_token": e["person_token"],
                          "team_cd": teams[0] if len(teams) == 1 else None,
                          "status": "confirmed" if len(teams) == 1 else ("conflict" if teams else "no_team"),
                          "team_candidates": teams, "position": e["position"], "grade": e["grade"],
                          "doc_ids": e["doc_ids"]})
    for m in metas:
        m["owner_emp_id"] = ids.get(m["owner_token"])
    return metas, employees


# ---- DB ---------------------------------------------------------------------
LOAD_SQL = """SELECT doc_id, min(doc_type), string_agg(masked_text, E'\\n' ORDER BY chunk_idx)
              FROM rag.chunks GROUP BY doc_id ORDER BY doc_id"""
UPSERT_SQL = """INSERT INTO rag.doc_meta (doc_id, doc_type, teams, owner_token, owner_emp_id, status, rule_id)
                VALUES (%(doc_id)s, %(doc_type)s, %(teams)s, %(owner_token)s, %(owner_emp_id)s, %(status)s, %(rule_id)s)
                ON CONFLICT (doc_id) DO UPDATE SET doc_type = EXCLUDED.doc_type, teams = EXCLUDED.teams,
                  owner_token = EXCLUDED.owner_token, owner_emp_id = EXCLUDED.owner_emp_id,
                  status = EXCLUDED.status, rule_id = EXCLUDED.rule_id, updated_at = now()"""


def run(write_db=True, out_dir=None):
    """rag.chunks 전체로 메타데이터를 다시 만든다. 적재(starter.ingest) 마지막에 자동 호출된다."""
    from . import db
    out_dir = Path(out_dir or config.OUT_DIR)
    with db.ingest_conn() as conn, conn.cursor() as cur:
        cur.execute(LOAD_SQL)
        docs = [{"doc_id": r[0], "doc_type": r[1], "text": r[2]} for r in cur.fetchall()]
        tokens = sorted({t for d in docs for t in PERSON_RE.findall(d["text"])})
        cur.execute("SELECT token, normalized FROM vault.token_map WHERE token = ANY(%s)", (tokens,))
        names = dict(cur.fetchall())
        metas, employees = build_all(docs, DocMetaBuilder(names=names))
        if write_db:
            cur.execute("DELETE FROM rag.doc_meta WHERE NOT (doc_id = ANY(%s))", ([m["doc_id"] for m in metas],))
            cur.executemany(UPSERT_SQL, metas)
        conn.commit()
    out_dir.mkdir(exist_ok=True)
    write_jsonl(out_dir / "doc_meta.jsonl", metas)
    write_jsonl(out_dir / "employees.jsonl", employees)
    summary = summarize(metas)
    print(f"문서 메타데이터 {len(metas)}건 (resolved {summary['resolved']}, team_only {summary['team_only']}, "
          f"restricted {summary['restricted']}), 직원 {len(employees)}명 → {out_dir}")
    return metas, employees


def summarize(metas):
    s = defaultdict(int)
    for m in metas:
        s[m["status"]] += 1
    return s


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_gold_ids(path):
    """채점 정답 파일에서 doc_id 만 읽는다 (엑셀 저장본 cp949 도 허용). 라벨 값은 쓰지 않는다."""
    import csv
    import io
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949")
    return {r["doc_id"] for r in csv.DictReader(io.StringIO(text))}


def run_offline(data_paths, ids=None, out_path=None):
    """DB 없이 원문 jsonl 을 적재와 같은 탐지기로 가명처리한 뒤 판정한다. 채점 전용.

    vault 에 쓰지 않으며, 출력 파일에는 채점을 위해 담당 직원 정규화 이름(owner_name)을 함께 적는다
    (outputs/ 는 Git 제외 로컬 폴더). 입력의 정답 라벨(entities)은 쓰지 않는다."""
    from . import pii_pipeline
    docs, names = [], {}
    for data_path in data_paths:
        for line in open(data_path, encoding="utf-8"):
            d = json.loads(line)
            doc_id = d.get("doc_id") or d["id"]          # dev/test 파일은 id, holdout 은 doc_id
            if ids and doc_id not in ids:
                continue
            masked, records = pii_pipeline.pseudonymize(d["text"], pii_pipeline.detect(d["text"]))
            for r in records:
                names[r["token"]] = r["normalized"]
            docs.append({"doc_id": doc_id, "doc_type": d.get("doc_type"), "text": masked})
    metas, _ = build_all(docs, DocMetaBuilder(names=names))
    for m in metas:
        m["owner_name"] = names.get(m["owner_token"])
    out_path = Path(out_path or Path(config.OUT_DIR) / "doc_meta_offline.jsonl")
    out_path.parent.mkdir(exist_ok=True)
    write_jsonl(out_path, metas)
    summary = summarize(metas)
    print(f"문서 메타데이터 {len(metas)}건 (resolved {summary['resolved']}, team_only {summary['team_only']}, "
          f"restricted {summary['restricted']}) → {out_path}")
    return metas


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-db-write", action="store_true", help="rag.doc_meta 를 갱신하지 않음")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--from-data", nargs="+", default=None, help="DB 대신 원문 jsonl(여러 개 가능)을 가명처리해 판정 (채점용)")
    ap.add_argument("--gold", default=None, help="--from-data 와 함께: 이 정답 파일의 doc_id 만 처리")
    ap.add_argument("--out", default=None, help="--from-data 출력 파일 (기본 outputs/doc_meta_offline.jsonl)")
    a = ap.parse_args()
    if a.from_data:
        run_offline(a.from_data, ids=read_gold_ids(a.gold) if a.gold else None, out_path=a.out)
    else:
        run(write_db=not a.no_db_write, out_dir=a.out_dir)


if __name__ == "__main__":
    main()
