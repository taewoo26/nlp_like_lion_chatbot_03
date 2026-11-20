"""인물 후보(vault.person_info) 판정: 같은 사람은 같은 reg_id 로 잇는다.

기준 문서: jungha/종합/인물사전/ 의 「person_info 규칙 정의서」(H1~H4, M1~M6, N1~N8)와
「인물 사전 설계 및 파이프라인」(용어·칼럼).  DB 를 쓰지 않는 순수 함수/클래스이다 (행은 dict).
DB 읽기·쓰기는 호출하는 쪽(적재·판정 배치)이 한다.

핵심 규칙
  - 행은 합치지 않는다. 같은 사람이면 같은 reg_id 로 잇는다 (이름 토큰이 달라도: H1).
  - 확정 인물(reg_id·person_registry)은 high(H1·H3·H4) 근거로만 만든다.
  - mid(M1~M6) 는 '추정 연결'이다. reg_id 를 만들지 않고 양쪽 evidence.mid_matched 에 {상대 person_id: 규칙}
    으로만 남긴다. 그 행은 unresolved · level=mid 가 되고, 나중에 high 근거가 생기면 그때 reg_id 를 받는다.
  - 이미 서로 다른 reg_id 를 가진 사람끼리 high 로 같은 사람이 되면 reg_id 를 하나로 바꾸고
    사라지는 쪽 registry 행의 merged_into 에 남긴다. 잇기 전에 N8(evidence.not_same_as 충돌)을 먼저 검사한다.
  - '후보 N개' 는 행 수가 아니라 사람 수로 센다. 같은 reg_id 이거나 추정 연결(mid)로 이어진 행들은 1명으로 센다.
    N8 검사도 추정 연결로 이어진 행들을 한 묶음으로 본다.
  - 행에 같은 사람 근거(H·M)와 다른 사람 판정(N)이 같이 있으면 level 은 H·M 의 것을 쓰고
    N 의 상대는 evidence.not_same_as 에만 남긴다. 둘 다 없으면 hold.

행(person_info) 필드
  person_id, doc_id(문서 ID 1개. DB 에는 TEXT[] {doc_id} 로 저장), token, role, info_label(list),
  evidence(dict), reg_id, resolution_status(pending|resolved|unresolved), created_at, updated_at
evidence = {"level", "rule", "method", "matched", "mid_matched", "not_same_as", "chunk_ids", "name_spans", "clues", "dt"}
  level  high|mid|none|hold   rule  판정에 쓴 규칙 번호   method  rule|manual   matched  high 로 연결된 상대 person_id
  mid_matched  추정 연결(mid)된 상대 person_id → 그 규칙(M1~M6)  예) {"tmp_B": "M1"}  (reg_id 없음)
  not_same_as  다른 사람으로 판정된 상대 person_id → 그 판정 규칙(N1~N8)  예) {"tmp_B": "N4"}
  chunk_ids  후보가 나온 청크   name_spans  이름 원문 위치 [start, end)
  clues  단서 토큰과 원문 위치 [{"token", "span"}]   dt  마지막 판정 시각
  (not_same_as 는 칼럼이 아니라 evidence 안에만 둔다)
registry 행 = {"reg_id", "reason", "merged_into", ...}. merged_into 는 DB 에서 reason.merged_into 로 저장한다.

토큰의 종류와 원문은 token_info = {token: {"label", "normalized"}} (vault.token_map) 로 본다.
라벨은 대소문자를 가리지 않는다 (PERSON / person).

v3 (PERSON_RULES=v3, 기본 v1 — v1 이면 아래 내용은 적용되지 않는다)
  - 추정(mid) 연결에도 reg_id 를 준다. evidence.level 은 mid 로 남고, registry.reason.confidence 는
    high 근거가 하나라도 있으면 high, 아니면 mid. registry.reason.path = "doc" (문서 간 연결).
  - M9  같은 이름 토큰 + 이름 옆 부서가 같음(evidence.dept, person_candidates 가 남김) → mid.
        같은 부서·같은 이름 후보가 서로 다른 사람 2명 이상이면 잇지 않고 검토로.
  - M7  같은 이름 토큰 + 같은 역할 그룹, M8  같은 이름 토큰 + 다른 사람 근거 없음 → mid.
        부서가 서로 다르면 M7·M8 로 잇지 않는다.
  - N9  동명이인 방어: 같은 이름 토큰의 행들 사이에 not_same_as(N1~N7)·N1~N4·부서 다름·같은 문서 안 분리가
        하나라도 있으면 그 이름은 M7·M8 을 쓰지 않는다 (판정 시작 때 전체 행으로 한 번 계산).
  - 구성원 충돌 확인: 두 묶음을 잇기 전에 양쪽 구성원 쌍(다른 문서) 중 하나라도 N1~N4 이면 잇지 않고 검토(N8).
  - registry 는 '여러 문서에 걸쳐 이어진 사람'이다. 이번 판정에서 새로 만든 reg_id 중 행이 모두 한 문서에
    있는 것(같은 문서 안 M4 연결만 있는 경우)은 판정 끝에 풀고 추정 연결(mid_matched)로 되돌린다.
  - 한계: 증분 적재에서는 새 충돌이 생겨도 기존 연결을 다시 평가하지 않는다 → 전체 재적재(--reset) 권장.

적용하지 않는 규칙 (입력 정보가 아직 없음)
  - N5 사내 소속 충돌: person_candidates가 같은 문서 안에서만 확인한다.
  - H3·N3: token_map 에 사번·고객번호 라벨(employee_id, customer_id)이 있을 때만 동작한다.
  - M3·N2 의 생년월일·성별: 전체 주민번호에서 계산한다. 마스킹 번호는 토큰이 되지 않으므로 대상이 아니다.
    별도 라벨(birth_date, gender)이 생기면 그 토큰도 읽는다.
"""
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

RRN = "resident_registration_number"
CLUE_LABELS = frozenset({"phone_number", RRN, "account_number", "card_number", "email"})   # info_label 허용 라벨
M1_LABELS = frozenset({"phone_number", "account_number", "email"})       # M1·M2 식별자
H4_LABELS = M1_LABELS | {"card_number"}                                    # H4 식별자 종류
ID_LABELS = frozenset({"employee_id", "customer_id"})                      # H3·N3 (있을 때만)
BIRTH_LABEL, GENDER_LABEL = "birth_date", "gender"
SURNAME2 = ("남궁", "황보", "제갈", "선우", "독고", "사공", "서문")
LEVEL_RANK = {"hold": 0, "none": 0, "mid": 1, "high": 2}


def is_given_of(short, full):
    """short 가 full 에서 성을 뗀 이름인가 (윤선영 ← 선영). 성은 1글자 또는 복성."""
    if not re.fullmatch(r"[가-힣]+", short or "") or not full or full == short or not full.endswith(short):
        return False
    gap = len(full) - len(short)
    return gap == 1 or (gap == 2 and full[:2] in SURNAME2)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def created_time(value):
    """DB datetime과 ISO 문자열을 같은 UTC 시각으로 비교한다."""
    if value is None:
        return None
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def next_created_at(previous=None, now=None):
    """시계가 뒤로 가거나 같은 시각이어도 기존 최대값보다 1μs 이상 늦게 발급한다."""
    stamp = created_time(now) if now is not None else datetime.now(timezone.utc)
    return max(stamp, created_time(previous) + timedelta(microseconds=1)) if previous else stamp


def creation_key(row, id_column):
    # 과거 동률은 ID로 결정적으로 정렬한다. 이 순서가 실제 과거 등록 순서라는 뜻은 아니다.
    return (created_time(row.get('created_at')) or datetime.max.replace(tzinfo=timezone.utc), row[id_column])


def rules_version(value=None):
    """PERSON_RULES=v1|v3 (기본 v1)."""
    v = (value or os.environ.get("PERSON_RULES") or "v1").strip().lower()
    if v not in ("v1", "v3"):
        raise ValueError(f"PERSON_RULES 는 v1 또는 v3 여야 합니다: {v}")
    return v


def mid_reg_default(version=None):
    """추정 연결에도 reg_id 를 주는가: v3 에서만."""
    return rules_version(version) == "v3"


def _env_on(name):
    return (os.environ.get(name, "off") or "off").strip().lower() == "on"


def new_evidence(chunk_ids=(), dt=None, name_spans=(), clues=()):
    """name_spans [[start, end), ...], clues [{"token", "span"}]: 원문 위치만 담는다 (평문 금지)."""
    return {"level": "hold", "rule": None, "method": "rule", "matched": [], "not_same_as": {},
            "chunk_ids": list(chunk_ids), "name_spans": [list(s) for s in name_spans],
            "clues": [dict(c) for c in clues], "dt": dt}


def not_same_as(row):
    """행의 not_same_as {상대 person_id: 규칙번호}. evidence 안에 있다."""
    return row["evidence"].setdefault("not_same_as", {})


def mid_matched(row):
    """행의 추정 연결(mid) {상대 person_id: 규칙번호}. 읽기 전용 (없으면 빈 dict, evidence 를 바꾸지 않음)."""
    return row["evidence"].get("mid_matched") or {}


def _add(seq, items):
    for x in items:
        if x not in seq:
            seq.append(x)


class PersonResolver:
    """rows(person_info) 의 pending 행을 판정해 reg_id 로 잇는다. rows·registry 는 제자리에서 고친다."""

    def __init__(self, rows, registry=None, token_info=None, role_group=None, links=(),
                 link_unique=None, link_given=None, clock=None, id_factory=None, mid_reg=None,
                 version=None, name_only=None):
        self.rows = rows
        self.by_id = {r["person_id"]: r for r in rows}
        self.registry = {r["reg_id"]: r for r in (registry if registry is not None else [])}
        self.registry_list = registry if registry is not None else []
        self.token_info = token_info or {}
        self.role_group = role_group if role_group is not None else _default_role_group()
        self.links = list(links)                       # 같은 문서 안 확정 연결 [(person_id, person_id, "M4")]
        self.link_unique = _env_on("PERSON_LINK_UNIQUE") if link_unique is None else link_unique   # M5
        self.link_given = _env_on("PERSON_LINK_GIVEN") if link_given is None else link_given       # M6
        self.version = rules_version(version)
        self.v3 = self.version == "v3"                                                              # M9·구성원 충돌
        self.name_only = self.v3 if name_only is None else (self.v3 and name_only)   # M7·M8 (실험용 인자)
        self.mid_reg = mid_reg_default(self.version) if mid_reg is None else mid_reg                # mid 도 reg_id
        self._name_conflict = set()                    # N9: 동명이인 가능성이 있는 이름 토큰
        self._new_regs = set()                         # 이번 판정에서 새로 만든 reg_id
        self.clock = clock or _now
        self.last_created_at = max((created_time(r['created_at']) for r in self.registry_list
                                    if r.get('created_at')), default=None)
        for r in self.registry_list:
            if not r.get('created_at'):
                self.last_created_at = next_created_at(self.last_created_at, self.clock())
                r['created_at'] = self.last_created_at
        self.id_factory = id_factory or (lambda prefix: f"{prefix}{uuid.uuid4()}")
        self.review = []                               # 합치지 않고 사람이 볼 것
        self.waiting = set()
        self.touched = {}
        for r in rows:                                 # 이미 합쳐진(merged_into) registry 를 가리키는 행은 살아남은 쪽으로
            r["reg_id"] = self._root(r.get("reg_id"))
        # 사람 단위 묶음: 같은 reg_id(확정) 또는 추정 연결(mid)로 이어진 행들. 후보 수 세기와 N8 검사에 쓴다.
        self._uf = {}
        for r in rows:
            if r["reg_id"]:
                self._uf_union(r["person_id"], "reg:" + r["reg_id"])
            for p in mid_matched(r):
                if p in self.by_id:
                    self._uf_union(r["person_id"], p)

    # ------------------------------------------------------------ 실행
    def resolve(self, rejudge_hold=False, rejudge_unresolved=False):
        """pending을 판정한다. rejudge_hold는 hold, rejudge_unresolved는 none까지 재판정한다.

        none 상대와의 연결 금지는 유지한다. none 후보도 제3의 후보와는 동일인일 수 있다.
        관리자 판정(method=manual)은 자동 재판정에서 제외한다.
        """
        ordered = sorted(enumerate(self.rows), key=lambda pair:
                         (0, creation_key(pair[1], 'person_id')) if pair[1].get('created_at')
                         else (1, pair[0]))
        todo = [r for _, r in ordered if r["resolution_status"] == "pending"
                or (rejudge_unresolved and r['resolution_status'] == 'unresolved'
                    and r['evidence'].get('method') != 'manual')
                or (rejudge_hold and r["resolution_status"] == "unresolved" and not r["reg_id"]
                    and r["evidence"].get("level") == "hold")]
        self.waiting = {r["person_id"] for r in todo}
        if self.name_only:
            self._name_conflict = self._conflicted_tokens()
        for a in todo:
            self.waiting.discard(a["person_id"])
            self._touch(a)
            self._evaluate(a)
            self._settle_all()
        if self.mid_reg:
            self._drop_single_doc_regs()
        return {"rows": self.rows, "registry": self.registry_list, "review": self.review,
                "look_up": build_look_up(self.rows)}

    def _evaluate(self, a):
        pool = [b for b in self.rows if b is not a and b["person_id"] not in self.waiting]
        cross = [b for b in pool if b["doc_id"] != a["doc_id"]]
        for x, y, rule in self.links:                  # 같은 문서 안 연결 (M4, mid → 추정 연결). 나중에 판정되는 행이 맺는다
            if a["person_id"] in (x, y):
                peer = self.by_id[y if x == a["person_id"] else x]
                if peer["person_id"] not in self.waiting:
                    self._link(a, peer, "mid", rule)
        self._drop_shared_ids(a, cross)
        h1 = [b for b in cross if self._tok(a, {RRN}) & self._tok(b, {RRN})]
        same = [b for b in cross if b["token"] == a["token"] and b not in h1]
        for b in same:                                 # N1~N4: 다른 사람으로 확인
            n = self._n_rule(a, b)
            if n:
                self._mark_diff(a, b, n)
        self._link_all(a, [(b, "H1") for b in h1], "high")   # H1: 이름이 달라도 같은 사람
        same = [b for b in same if not self._blocked(a, b)]
        highs = []
        for b in same:                                 # H3·H4
            rule = self._high_rule(a, b, same)
            if rule == "REVIEW":
                self._add_review(a, "H4", [b], "같은 식별 토큰을 가진 같은 이름 사람이 더 있음")
            elif rule:
                highs.append((b, rule))
        self._link_all(a, highs, "high")
        self._stage_m1(a, same)
        self._stage_m3(a, same)
        if self.v3:
            self._stage_dept(a, same)
        if self.name_only:
            self._stage_name(a, same)
        self._stage_given(a, cross)
        if not a["reg_id"] and self.link_unique:
            self._stage_m5(a, pool)
        for b in cross:                                # a 가 성명이면, 이미 있던 '이름만' 행의 후보가 늘었다
            if not b["reg_id"] and b["person_id"] not in self.waiting \
                    and is_given_of(self._norm(b), self._norm(a)) and b["token"] != a["token"]:
                self._touch(b)
                self._stage_given(b, [x for x in self.rows if x is not b and x["doc_id"] != b["doc_id"]
                                      and x["person_id"] not in self.waiting])

    # ------------------------------------------------------------ 규칙
    def diff_rule(self, a, b):
        """a·b 가 N1~N4 로 다른 사람인가. 걸린 규칙 번호 하나 또는 None (문서 안 N7 판정에도 쓴다)."""
        return (self._n_rule(a, b) or [None])[0]

    def _n_rule(self, a, b):
        """N1~N4 중 먼저 걸리는 규칙 번호의 리스트 (없으면 [])."""
        ra, rb = self._tok(a, {RRN}), self._tok(b, {RRN})
        if ra and rb and not ra & rb:
            return ["N1"]
        (ba, ga), (bb, gb) = self._birth_gender(a), self._birth_gender(b)
        if (ba and bb and not ba & bb) or (ga and gb and not ga & gb):
            return ["N2"]
        for kind in ID_LABELS:
            ta, tb = self._tok(a, {kind}), self._tok(b, {kind})
            if ta and tb and not ta & tb:
                return ["N3"]
        ga_, gb_ = self._group_of(a), self._group_of(b)
        if ga_ and gb_ and ga_ != gb_:
            return ["N4"]
        return []

    def _high_rule(self, a, b, same):
        """H3·H4. 걸리면 규칙 번호, 검토면 "REVIEW", 아니면 None."""
        for kind in ID_LABELS:
            if self._tok(a, {kind}) & self._tok(b, {kind}):
                return "H3"
        overlap = set(a["info_label"]) & set(b["info_label"])
        if len({self._label(t) for t in overlap} & H4_LABELS) >= 2:
            others = [r for r in same if self._pkey(r) != self._pkey(b) and not self._blocked(a, r)
                      and set(r["info_label"]) & overlap]
            return "REVIEW" if others else "H4"
        return None

    def _stage_m1(self, a, same):
        """M1: 식별자 1개 + 이름 일치. 같은 식별 토큰을 가진 사람이 2명 이상이면 hold 로 두고 검토."""
        mine, own = self._tok(a, M1_LABELS), self._own(a)
        shared = [b for b in same if b["person_id"] not in own and mine & self._tok(b, M1_LABELS)]
        persons = {self._pkey(b) for b in shared}
        if len(persons) > 1:
            self._add_review(a, "M1", shared, "같은 식별 토큰·이름의 후보가 2명 이상")
        elif shared:
            self._link(a, shared[0], "mid", "M1", [b["person_id"] for b in shared])

    def _stage_dept(self, a, same):
        """M9 (v3): 같은 이름 + 이름 옆 부서가 같음 → 추정. 그런 후보가 서로 다른 사람 2명 이상이면 검토."""
        da = self._dept_of(a)
        if not da:
            return
        own = self._own(a)
        hits = [b for b in same if b["person_id"] not in own and not self._blocked(a, b) and self._dept_of(b) == da]
        persons = {self._pkey(b) for b in hits}
        if len(persons) > 1:
            self._add_review(a, "M9", hits, "같은 이름·같은 부서의 후보가 2명 이상")
        elif hits:
            self._link(a, hits[0], "mid", "M9", [b["person_id"] for b in hits])

    def _member_conflict(self, a, b):
        """v3: a 묶음과 b 묶음의 구성원(다른 문서) 사이에 N1~N4 가 있는가."""
        ga, gb = self._soft_members(a), self._soft_members(b)
        return any(x["doc_id"] != y["doc_id"] and self._n_rule(x, y) for x in ga for y in gb)

    def _stage_name(self, a, same):
        """M7·M8: 같은 이름 토큰이고 다른 사람 근거가 없으면 추정 연결. N9(동명이인 가능성) 이름은 건너뛴다.
        same 은 N1~N4 로 막힌 행을 이미 뺀 같은 이름 토큰의 다른 문서 행이다."""
        if a["token"] in self._name_conflict:
            return
        own, da = self._own(a), self._dept_of(a)
        seen = set()
        for b in same:
            if b["person_id"] in own or self._blocked(a, b):
                continue
            db = self._dept_of(b)
            if da and db and da != db:                 # 부서가 다르면 약한 근거로 잇지 않음
                continue
            key = self._pkey(b)
            if key in seen or key == self._pkey(a):
                continue
            seen.add(key)
            ga, gb = self._group_of(a), self._group_of(b)
            self._link(a, b, "mid", "M7" if ga and gb and ga == gb else "M8")

    def _conflicted_tokens(self):
        """N9: 같은 이름 토큰의 행 사이에 다른 사람 근거가 하나라도 있는 토큰.
        근거 = not_same_as(문서 안 N5~N7 포함), N1~N4, 부서 다름, 같은 문서 안에서 따로 남은 행(누구인지 모름)."""
        by_token = {}
        for r in self.rows:
            by_token.setdefault(r["token"], []).append(r)
        out = set()
        for token, rows in by_token.items():
            for i, a in enumerate(rows):
                for b in rows[i + 1:]:
                    da, db = self._dept_of(a), self._dept_of(b)
                    if a["doc_id"] == b["doc_id"] or self._blocked(a, b) or self._n_rule(a, b) \
                            or (da and db and da != db):
                        out.add(token)
                        break
                if token in out:
                    break
        return out

    def _stage_m3(self, a, same):
        """M3: 생년월일·성별이 양쪽에 있고 모두 같음."""
        ba, ga = self._birth_gender(a)
        if not (ba and ga):
            return
        own = self._own(a)
        for b in same:
            bb, gb = self._birth_gender(b)
            if b["person_id"] not in own and bb and gb and ba & bb and ga & gb:
                self._link(a, b, "mid", "M3")
                return

    def _stage_given(self, a, cross):
        """'이름만' 쓴 행 a 를 성명 행과 잇는다: M2(식별자 공유), M6(직원 + 후보 1명, 스위치)."""
        own = self._own(a)
        full = [b for b in cross if b["token"] != a["token"] and is_given_of(self._norm(a), self._norm(b))
                and not self._blocked(a, b) and b["person_id"] not in own]
        if not full:
            return
        mine = self._tok(a, M1_LABELS)
        alive = []
        for b in full:                                 # N7: N1~N6 이 걸린 후보는 그 후보와만 none
            n = self._n_rule(a, b)
            if n:
                self._mark_diff(a, b, ["N7"])
            else:
                alive.append(b)
        shared = [b for b in alive if mine & self._tok(b, M1_LABELS)]
        persons = {self._pkey(b) for b in shared}
        if len(persons) > 1:
            self._add_review(a, "M2", shared, "같은 식별 토큰에 이름 부분이 맞는 성명 후보가 2명 이상")
            return
        if shared:
            self._link(a, shared[0], "mid", "M2", [b["person_id"] for b in shared])
            return
        if self.link_given and self._group_of(a) == "staff" and len({self._pkey(b) for b in alive}) == 1 and alive:
            self._link(a, alive[0], "mid", "M6", [b["person_id"] for b in alive])

    def _stage_m5(self, a, pool):
        """M5: 식별 근거는 없지만 같은 이름 토큰의 기존 사람이 정확히 1명."""
        same = [b for b in pool if b["token"] == a["token"]]
        persons = {self._pkey(b) for b in same}
        if len(persons) != 1:
            return
        b = same[0]
        if self._blocked(a, b) or self._n_rule(a, b) or (a["role"] in (None, "unknown") and b["role"] in (None, "unknown")):
            return
        self._link(a, b, "mid", "M5", [x["person_id"] for x in same])

    def _drop_shared_ids(self, a, cross):
        """M1 '이름이 다를 때': 식별자는 같은데 이름이 다르면 그 토큰을 양쪽 info_label 에서 뺀다 (가족·대리인)."""
        for b in cross:
            if b["token"] == a["token"] or is_given_of(self._norm(a), self._norm(b)) \
                    or is_given_of(self._norm(b), self._norm(a)):
                continue
            shared = self._tok(a, M1_LABELS) & self._tok(b, M1_LABELS)
            if shared:
                for r in (a, b):
                    r["info_label"] = [t for t in r["info_label"] if t not in shared]
                    if "clues" in r["evidence"]:
                        r["evidence"]["clues"] = [c for c in r["evidence"]["clues"] if c["token"] not in shared]
                    self._touch(r)

    # ------------------------------------------------------------ 연결·기록
    def _link_all(self, a, pairs, level):
        """a 를 여러 상대와 잇는다. 상대 중 서로 none 인 사람이 있으면 어느 쪽인지 알 수 없으므로 아무와도 잇지 않는다."""
        groups = {}
        for b, _ in pairs:                             # 사람 단위(확정 reg_id + 추정 연결)로 묶어 서로 none 인지 본다
            groups.setdefault(self._pkey(b), self._soft_members(b))
        keys = list(groups)
        for i, ki in enumerate(keys):
            ids = {r["person_id"] for r in groups[ki]}
            for kj in keys[i + 1:]:
                if any(set(not_same_as(r)) & ids for r in groups[kj]):
                    self._add_review(a, "N8", [b for b, _ in pairs], "상대 후보끼리 서로 다른 사람이라 어느 쪽과도 잇지 않음")
                    return
        for b, rule in pairs:
            self._link(a, b, level, rule)

    def _own(self, row):
        """이미 같은 사람(확정 또는 추정)으로 이어진 행들의 person_id."""
        return {r["person_id"] for r in self._soft_members(row)}

    def _link(self, a, b, level, rule, matched=None):
        """a·b 를 같은 사람으로 잇는다. high 는 reg_id 로, mid 는 추정 연결(mid_matched)로만.
        N8 에 걸리면 잇지 않고 검토로 넘긴다. 잇지 않으면 False."""
        if a is b:
            return False
        if self.v3 and self._pkey(a) != self._pkey(b) and self._member_conflict(a, b):
            self._add_review(a, "N8", [b], "묶음 구성원 사이에 다른 사람 근거(N1~N4)가 있음 — 잇지 않음")
            return False
        if level == "mid" and not self.mid_reg:
            return self._mid_link(a, b, rule, matched)
        if self._pkey(a) != self._pkey(b) and self._conflict(a, b):
            self._add_review(a, "N8", [b], "합치면 not_same_as(none) 판정이 깨짐 — 합치지 않음")
            return False
        ga, gb = self._members(a), self._members(b)
        if not (a["reg_id"] and a["reg_id"] == b["reg_id"]):
            self._join(a, b, ga, gb, rule, level)
        elif self.mid_reg and level == "high":
            self._raise_confidence(a["reg_id"])
        for row, other in ((a, b), (b, a)):
            self._record(row, level, rule, matched if row is a and matched else [other["person_id"]])
        if rule == "H1" and a["token"] != b["token"]:          # H1: 이름 토큰이 달라도 같은 사람 → registry.reason 에 name_diff
            reg = self.registry.get(a["reg_id"])
            if reg is not None:
                reg["reason"]["name_diff"] = True
                reg["updated_at"] = self.clock()
        self._propagate(self._members(a))
        return True

    def _mid_link(self, a, b, rule, matched=None):
        """추정 연결(mid): reg_id 를 만들지 않고 양쪽 evidence.mid_matched 에 상대와 규칙을 남긴다.
        matched 는 같은 사람으로 센 상대들(b 포함)이다. 추정 묶음끼리 not_same_as 가 있으면 잇지 않는다 (N8)."""
        partners = [self.by_id[p] for p in (matched or []) if p in self.by_id and p != a["person_id"]]
        if b not in partners:
            partners.insert(0, b)
        if self._pkey(a) != self._pkey(b) and self._conflict(a, b):
            self._add_review(a, "N8", [b], "추정 연결하면 not_same_as(none) 판정이 깨짐 — 잇지 않음")
            return False
        for p in partners:
            for x, y in ((a, p), (p, a)):
                x["evidence"].setdefault("mid_matched", {}).setdefault(y["person_id"], rule)
                self._touch(x)
            self._uf_union(a["person_id"], p["person_id"])
        return True

    def _join(self, a, b, ga, gb, rule, level="high"):
        ra, rb = a["reg_id"], b["reg_id"]
        if not ra and not rb:
            reg = self.id_factory("reg_")
            self.last_created_at = next_created_at(self.last_created_at, self.clock())
            reason = {"method": "rule", "reason_code": rule}
            if self.mid_reg:                           # 추정(mid)으로 만든 인물인지 구분
                reason["confidence"] = level
            if self.v3:
                reason["path"] = "doc"                 # 문서 간 연결 (마스터 대조는 "master")
            row = {"reg_id": reg, "reason": reason, "merged_into": None,
                   "created_at": self.last_created_at, "updated_at": self.clock()}
            self.registry[reg] = row
            self.registry_list.append(row)
            self._new_regs.add(reg)
        elif not ra or not rb:
            reg = ra or rb
        else:                                           # 서로 다른 확정 인물 → 먼저 등록된 쪽으로 하나로
            reg, loser = sorted((ra, rb), key=lambda rid: creation_key(self.registry[rid], 'reg_id'))
            self.registry[loser]["merged_into"] = reg
            self.registry[loser]["updated_at"] = self.clock()
            self._uf_union("reg:" + loser, "reg:" + reg)
        if reg in self.registry:
            self.registry[reg]["updated_at"] = self.clock()
            if self.mid_reg and (level == "high" or any(
                    (self.registry.get(x) or {}).get("reason", {}).get("confidence") == "high" for x in (ra, rb) if x)):
                self._raise_confidence(reg)
        for r in ga + gb:
            r["reg_id"] = reg
            self._uf_union(r["person_id"], "reg:" + reg)
            self._touch(r)

    def _drop_single_doc_regs(self):
        """v3: 이번에 새로 만든 reg_id 중 행이 모두 한 문서에 있는 것을 풀어 추정 연결로 되돌린다.
        (registry = 여러 문서에 걸쳐 이어진 사람. 기존 DB 의 reg_id 는 건드리지 않는다)"""
        absorbed = {r.get("merged_into") for r in self.registry_list if r.get("merged_into")}
        now = self.clock()
        for reg in sorted(self._new_regs):
            entry = self.registry.get(reg)
            if entry is None or entry.get("merged_into") or reg in absorbed:
                continue
            members = [r for r in self.rows if r["reg_id"] == reg]
            if len({r["doc_id"] for r in members}) != 1:
                continue
            rule = entry["reason"].get("reason_code") or "M4"
            for r in members:
                for other in members:
                    if other is not r:
                        r["evidence"].setdefault("mid_matched", {}).setdefault(other["person_id"], rule)
            for r in members:
                ev = r["evidence"]
                r["reg_id"], r["resolution_status"] = None, "unresolved"
                ev["level"], ev["rule"], ev["matched"] = "mid", next(iter(ev["mid_matched"].values())), []
                ev["dt"] = r["updated_at"] = now
            self.registry_list.remove(entry)
            del self.registry[reg]

    def _raise_confidence(self, reg):
        """mid 로 만든 registry 에 high 근거가 생기면 confidence 를 high 로 올린다 (v3)."""
        reason = self.registry[reg]["reason"]
        if reason.get("confidence") != "high":
            reason["confidence"] = "high"
            self.registry[reg]["updated_at"] = self.clock()

    def _record(self, row, level, rule, matched):
        """reg_id 연결 기록. 처음 그 level 을 준 규칙을 유지하고 상대를 matched 에 더한다.
        level 은 올라가기만 한다 (high 다음에 온 mid 가 high 를 덮지 않음)."""
        ev = row["evidence"]
        if LEVEL_RANK.get(ev.get("level"), 0) < LEVEL_RANK[level]:
            ev["level"], ev["rule"] = level, rule
        _add(ev.setdefault("matched", []), matched)
        self._touch(row)

    def _mark_diff(self, a, b, codes):
        """a·b 를 다른 사람으로 기록한다 (서로의 evidence.not_same_as: 상대 → 규칙).
        이미 같은 사람(확정 reg_id 또는 추정 연결)으로 이어져 있으면 기록하지 않고 검토로."""
        if self._pkey(a) == self._pkey(b):
            self._add_review(a, "N8", [b], f"{'·'.join(codes)} 이지만 이미 같은 사람(확정 또는 추정)으로 이어져 있음")
            return
        for x, y in ((a, b), (b, a)):
            not_same_as(x).setdefault(y["person_id"], codes[0])
            self._touch(x)
        self._propagate(self._members(a) + self._members(b))

    def _propagate(self, group):
        """같은 사람으로 이어진 행들은 서로의 not_same_as 상대를 이어받는다 (N8 상속). 규칙은 원래 판정의 것을 쓴다."""
        by_reg = {}
        for r in group:
            by_reg.setdefault(r["reg_id"] or r["person_id"], []).append(r)
        for members in by_reg.values():
            ids = {r["person_id"] for r in members}
            inherited = {}
            for r in members:
                for x, rule in not_same_as(r).items():
                    if x not in ids:
                        inherited.setdefault(x, rule)
            for r in members:
                mine = not_same_as(r)
                if set(inherited) - set(mine):
                    for x, rule in inherited.items():
                        mine.setdefault(x, rule)
                    self._touch(r)
            for x, rule in inherited.items():
                other = self.by_id.get(x)
                if other:
                    theirs = not_same_as(other)
                    if ids - set(theirs):
                        for pid in sorted(ids):
                            theirs.setdefault(pid, rule)
                        self._touch(other)

    def _settle_all(self):
        now = self.clock()
        for row in self.touched.values():
            ev = row["evidence"]
            if row["reg_id"]:
                row["resolution_status"] = "resolved"
            else:
                row["resolution_status"] = "unresolved"
                if mid_matched(row):                    # 추정 연결: 같은 사람 근거(M)가 다른 사람 판정(N)보다 앞선다
                    ev["level"] = "mid"
                    ev["rule"] = next(iter(mid_matched(row).values()))
                elif not_same_as(row):
                    ev["level"] = "none"
                    ev["rule"] = next(iter(not_same_as(row).values()))
                else:
                    ev["level"], ev["rule"] = "hold", None
                ev["matched"] = []
            ev["dt"] = row["updated_at"] = now
        self.touched = {}

    def _add_review(self, a, rule, others, reason):
        item = {"person_id": a["person_id"], "rule": rule, "reason": reason,
                "others": sorted(o["person_id"] for o in others)}
        if item not in self.review:
            self.review.append(item)
        saved = a['evidence'].setdefault('review', [])
        if item not in saved:
            saved.append(item)
            self._touch(a)

    # ------------------------------------------------------------ 보조
    def _touch(self, row):
        self.touched[row["person_id"]] = row

    def _root(self, reg):
        while reg and (self.registry.get(reg) or {}).get("merged_into"):
            reg = self.registry[reg]["merged_into"]
        return reg

    def _members(self, row):
        """같은 reg_id(확정 인물)로 이어진 행들."""
        return [r for r in self.rows if r["reg_id"] == row["reg_id"]] if row["reg_id"] else [row]

    def _dept_of(self, row):
        """이름 옆 부서 (person_candidates 가 evidence.dept 에 남긴 값). 없으면 None."""
        return row["evidence"].get("dept")

    def _key(self, row):
        return row["reg_id"] or row["person_id"]            # 확정 인물 단위: reg_id 하나, 이어지지 않은 행은 각자 1명

    def _uf_find(self, x):
        root = self._uf.setdefault(x, x)
        while root != self._uf[root]:
            root = self._uf[root]
        while self._uf[x] != root:                          # 경로 압축
            self._uf[x], x = root, self._uf[x]
        return root

    def _uf_union(self, x, y):
        rx, ry = self._uf_find(x), self._uf_find(y)
        if rx != ry:
            self._uf[rx] = ry

    def _pkey(self, row):
        """사람 단위 키: 같은 reg_id 이거나 추정 연결(mid)로 이어진 행들은 같은 키."""
        return self._uf_find(row["person_id"])

    def _soft_members(self, row):
        """같은 사람(확정 또는 추정)으로 이어진 행들."""
        key = self._pkey(row)
        return [r for r in self.rows if self._pkey(r) == key]

    def _conflict(self, a, b):
        """a 쪽 묶음과 b 쪽 묶음(확정 reg_id + 추정 연결) 사이에 not_same_as 가 있는가 (N8)."""
        ga, gb = self._soft_members(a), self._soft_members(b)
        ids_a, ids_b = {r["person_id"] for r in ga}, {r["person_id"] for r in gb}
        return any(set(not_same_as(r)) & ids_b for r in ga) or any(set(not_same_as(r)) & ids_a for r in gb)

    def _blocked(self, a, b):
        return b["person_id"] in not_same_as(a) or a["person_id"] in not_same_as(b)

    def _label(self, token):
        return (self.token_info.get(token, {}).get("label") or "").lower()

    def _norm(self, row):
        return self.token_info.get(row["token"], {}).get("normalized") or ""

    def _tok(self, row, labels):
        return {t for t in row["info_label"] if self._label(t) in labels}

    def _group_of(self, row):
        group = self.role_group.get(row.get("role") or "unknown")
        return group if group in ("customer", "staff", "related") else None

    def _birth_gender(self, row):
        births, genders = set(), set()
        for t in row["info_label"]:
            label, value = self._label(t), re.sub(r"\D", "", self.token_info.get(t, {}).get("normalized") or "")
            if label == RRN and len(value) >= 7:
                century = {"1": 1900, "2": 1900, "5": 1900, "6": 1900, "3": 2000, "4": 2000, "7": 2000, "8": 2000,
                           "9": 1800, "0": 1800}[value[6]]
                births.add(f"{century + int(value[:2])}{value[2:6]}")
                genders.add("M" if value[6] in "13579" else "F")
            elif label == BIRTH_LABEL and len(value) == 8:
                births.add(value)
            elif label == GENDER_LABEL:
                raw = (self.token_info.get(t, {}).get("normalized") or "").strip().upper()
                if raw[:1] in ("M", "남"):
                    genders.add("M")
                elif raw[:1] in ("F", "여"):
                    genders.add("F")
        return births, genders


def _default_role_group():
    from .roles import default_rules
    return default_rules().role_group


def build_look_up(rows):
    """rag.look_up 행: 분석이 끝난(resolved·unresolved) 후보 × 후보가 나온 청크. pending 은 넣지 않는다."""
    out = []
    for r in rows:
        if r["resolution_status"] == "pending":
            continue
        for chunk_id in r["evidence"].get("chunk_ids", []):
            out.append({"chunk_id": chunk_id, "person_id": r["person_id"], "token": r["token"],
                        "reg_id": r["reg_id"], "role": r["role"]})
    return out


def check_look_up(rows, look_up, chunks):
    """look_up 이 원본 후보·청크와 맞는지 확인한다 (설계서 6.1 문서 일치·복사 정보). 어긋나면 ValueError.

    - look_up 의 token·reg_id·role 은 person_info 와 같아야 한다
    - look_up 의 chunk_id 는 실제 청크여야 하고, 그 청크의 doc_id 는 후보의 doc_id 와 같아야 한다
    - pending 후보는 look_up 에 없어야 한다
    chunks: [{"chunk_id", "doc_id", ...}]
    """
    by_id = {r["person_id"]: r for r in rows}
    chunk_doc = {c["chunk_id"]: c["doc_id"] for c in chunks}
    seen = set()
    for l in look_up:
        key = (l["chunk_id"], l["person_id"])
        if key in seen:
            raise ValueError(f"look_up 중복: {key}")
        seen.add(key)
        r = by_id.get(l["person_id"])
        if r is None:
            raise ValueError(f"look_up 이 없는 후보를 가리킴: {l['person_id']}")
        if r["resolution_status"] == "pending":
            raise ValueError(f"{r['person_id']}: pending 후보가 look_up 에 있음")
        for col in ("token", "reg_id", "role"):
            if l[col] != r[col]:
                raise ValueError(f"{r['person_id']}: look_up.{col} 이 person_info 와 다름")
        if l["chunk_id"] not in chunk_doc:
            raise ValueError(f"{r['person_id']}: 없는 청크 {l['chunk_id']}")
        if chunk_doc[l["chunk_id"]] != r["doc_id"]:
            raise ValueError(f"{r['person_id']}: 청크 {l['chunk_id']} 의 문서가 후보의 문서와 다름")


def check_rows(rows, registry=(), token_info=None, mid_reg=None):
    """판정 결과가 설계의 제약과 맞는지 확인한다 (어긋나면 ValueError). 시험·배치 끝 검증용.

    token_info 를 주면 DDL 에 FK 가 없는 부분도 확인한다:
    person_info.token 은 token_map 의 person 토큰, info_label 은 token_map 에 있는 단서 라벨(CLUE_LABELS) 토큰.
    """
    regs = {r["reg_id"]: r for r in registry}
    by_id = {r["person_id"]: r for r in rows}
    mid_reg = mid_reg_default() if mid_reg is None else mid_reg
    for r in rows:
        if token_info is not None:
            if (token_info.get(r["token"], {}).get("label") or "").lower() != "person":
                raise ValueError(f"{r['person_id']}: token 이 token_map 의 이름 토큰이 아님")
            for t in r["info_label"]:
                if (token_info.get(t, {}).get("label") or "").lower() not in CLUE_LABELS:
                    raise ValueError(f"{r['person_id']}: info_label 에 허용되지 않은 토큰 {t}")
        status, level, reg = r["resolution_status"], r["evidence"].get("level"), r["reg_id"]
        if status not in ("pending", "resolved", "unresolved"):
            raise ValueError(f"{r['person_id']}: 알 수 없는 상태 {status}")
        if (status == "resolved") != bool(reg):
            raise ValueError(f"{r['person_id']}: resolved 일 때만 reg_id 가 있어야 함")
        if reg and (reg not in regs or regs[reg].get("merged_into")):
            raise ValueError(f"{r['person_id']}: reg_id 가 없거나 이미 합쳐진 registry 를 가리킴")
        # 확정 인물(resolved)은 high 만. mid 는 reg_id 없는 추정 연결(unresolved)이다.
        # v3 이면 mid 도 reg_id 를 받아 resolved 가 된다.
        allowed = {"pending": {"hold"}, "resolved": {"high", "mid"} if mid_reg else {"high"},
                   "unresolved": {"mid", "none", "hold"}}[status]
        if level not in allowed:
            hint = (" (mid 는 reg_id 를 받지 않습니다. 기준 변경 전에 적재한 데이터라면 --reset 으로 다시 적재하세요)"
                     if status == "resolved" and level == "mid" else "")
            raise ValueError(f"{r['person_id']}: {status} 인데 level 이 {level}{hint}")
        if status == "unresolved" and (level == "mid") != bool(mid_matched(r)):
            raise ValueError(f"{r['person_id']}: 추정 연결(mid_matched)이 있을 때만 level 이 mid 여야 함")
        for x in mid_matched(r):
            if x not in by_id or r["person_id"] not in mid_matched(by_id[x]):
                raise ValueError(f"{r['person_id']}: mid_matched 가 서로 기록되지 않음 ({x})")
            if x in not_same_as(r):
                raise ValueError(f"{r['person_id']}: 같은 상대가 mid_matched 와 not_same_as 에 함께 있음 ({x})")
        for x in not_same_as(r):
            if x not in by_id or r["person_id"] not in not_same_as(by_id[x]):
                raise ValueError(f"{r['person_id']}: not_same_as 가 서로 기록되지 않음 ({x})")
            if reg and by_id[x]["reg_id"] == reg:
                raise ValueError(f"{r['person_id']}: 같은 사람인데 not_same_as 에 있음 ({x})")
