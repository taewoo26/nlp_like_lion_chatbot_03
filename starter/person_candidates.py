"""문서 1건 안의 인물 후보(vault.person_info 행) 만들기: 같은 문서 안 판정 H2 · N6 · M4 (판정 규칙 정의서).

입력은 pseudonymize() 의 records 와 청크 목록이다. DB 를 쓰지 않고 행(dict)만 만든다.
  - H2  같은 문서의 같은 성명 반복은 한 행으로 (doc_id 는 한 번만)
        단서(역할·식별자·직급·소속·A/B 표기)가 있는 언급끼리 먼저 묶고, 단서가 없는 언급은
        붙을 수 있는 묶음이 정확히 1개일 때만 붙인다. 2개 이상이면 따로 두고 none 표시는 하지 않는다 (hold).
  - N6  같은 줄 역할 그룹이 다름 / 같은 종류 번호가 다름 / 직급·A/B·'동명이인' 표기 /
        서로 상대하는 관계 (같은 줄 주어·목적어, 메일의 받는 사람 '○○ 씨,' ↔ 보내는 사람 '○○ 드림')
  - N5  둘 다 사내(staff)인데 이름 옆에 적힌 부서가 다름, 또는 한쪽은 사내 부서·다른 쪽은 외부 조직
        (수탁사·협력사·외부 등) 표시 (같은 문서만. 작성일이 없는 문서 간 비교는 하지 않음)
        → 행을 나누고 서로의 evidence.not_same_as 에 기록 (규칙 번호 N5 또는 N6)
  - M4  이름만 쓴 개체('민준')는 그 문서에 이름 부분이 맞는 성명 개체가 정확히 1개일 때 그 행과 같은 사람
        → 행은 따로 두고 연결(links)만 돌려준다. reg_id 는 PersonResolver 가 붙인다.
        2개 이상이면 R3(hold), N1~N5 또는 받는 사람↔보내는 사람 관계면 N7(그 후보와만 none)
식별자(info_label)는 이름과 같은 줄에서만 모으고, 줄에 사람 언급이 2번 이상이거나 대표번호 같은 공유 번호는
처음부터 넣지 않는다. '내용·메모·요약·비고' 같은 서술 칸의 번호도 넣지 않는다.
새 행은 pending, level hold 로 시작한다 (판정은 PersonResolver).
"""
import re
import uuid
from datetime import datetime, timezone

from .person_resolve import CLUE_LABELS, PersonResolver, is_given_of, new_evidence
from .roles import _candidates, role_for_mention
_TOKEN = re.compile(r"<[A-Z_]+_[0-9A-Za-z]+>")
_SHARED_PHONE = re.compile(r"^(?:1[5-9]\d{6}|080\d+)$")        # 1588-xxxx 같은 대표번호, 수신자부담
_SUBJECT = re.compile(r"(?:께서|은|는|이|가)(?![가-힣])")
_OBJECT = re.compile(r"(?:에게서|에게|한테|께|와|과|를|을)(?![가-힣])")
_AB = re.compile(r"\s*[(\[]?\s*([A-Ca-c])\s*[)\]]?(?![A-Za-z가-힣0-9])")
NARRATIVE_LABELS = ("내용", "메모", "요약", "비고", "특이사항", "경위", "사유", "결과", "의견", "조치", "안내", "처리")
_SEGMENT_SEP = re.compile(r"\s/\s|\||;")                       # 한 줄 안의 칸 구분
_FIELD = re.compile(r"^\s*([^:：]{1,20}?)\s*[:：]\s*")
_DEPT = re.compile(r"[가-힣A-Za-z]{2,}(?:팀|부|실|센터|본부|지점)$")
_EXTERNAL = re.compile(r"^(?:수탁사|위탁사|협력사|협력업체|파트너사|외부|업체)(?:의|측|소속)?$")   # 외부 조직 표시
_ADDRESSEE = re.compile(r"\s*(?:씨|님)\s*[,，]")                 # '민준 씨,' — 메일의 받는 사람
_SIGNER = re.compile(r"\s*(?:드림|올림)\s*$")                     # '조민준 드림' — 메일의 보내는 사람


def is_shared_number(label, normalized):
    """대표번호처럼 여러 사람이 쓰는 번호는 단서로 쓰지 않는다."""
    return label == "phone_number" and bool(_SHARED_PHONE.match(normalized or ""))


def in_narrative_cell(text, pos):
    """pos 가 '내용: …', '메모: …' 같은 서술 칸 안인가 (정의서 0장: 메모·요약 칸의 번호는 단서로 쓰지 않는다)."""
    line_start = text.rfind("\n", 0, pos) + 1
    seg = max([line_start] + [line_start + m.end() for m in _SEGMENT_SEP.finditer(text[line_start:pos])])
    field = _FIELD.match(text[seg:pos])
    return bool(field and field.group(1).strip().endswith(NARRATIVE_LABELS))


def _chunk_ranges(doc_id, masked, chunks):
    ranges, cursor = [], 0
    for index, part in enumerate(chunks):
        start = masked.find(part, cursor)
        if start < 0:
            raise ValueError("가명 텍스트와 청크의 위치가 다름")
        ranges.append((start, start + len(part), f"{doc_id}#{index}"))
        cursor = start + len(part)
    return ranges


def build_doc_candidates(doc_id, original, masked, records, chunks, rules, id_factory=None, clock=None):
    """반환: (rows, links)
    rows  : person_info 행 목록 (pending). 이름 언급이 없는 문서는 빈 리스트.
    links : [(person_id, person_id, "M4")] 같은 문서 안에서 같은 사람으로 본 쌍
    """
    new_id = id_factory or (lambda prefix: f"{prefix}{uuid.uuid4()}")
    now = (clock or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")))()
    tokens = list(_TOKEN.finditer(masked))
    if [m.group() for m in tokens] != [r["token"] for r in records]:
        raise ValueError("가명 텍스트의 토큰 순서와 원문 records가 다름")
    ranges = _chunk_ranges(doc_id, masked, chunks)

    def line_of(pos):
        return original.count("\n", 0, pos)

    people = [(m, r) for m, r in zip(tokens, records) if r["label"] == "person"]
    spans = [{"start": r["start"], "end": r["end"]} for _, r in people]
    mentions = []
    for token_match, r in people:
        chunk_id = next((cid for s, e, cid in ranges if s <= token_match.start() and token_match.end() <= e), None)
        if chunk_id is None:
            raise ValueError("PERSON 토큰을 포함한 청크를 찾지 못함")
        role, _ = role_for_mention(original, r["start"], r["end"], rules,
                                   [s for s in spans if (s["start"], s["end"]) != (r["start"], r["end"])])
        mentions.append({"token": r["token"], "norm": r["normalized"], "start": r["start"], "end": r["end"],
                         "line": line_of(r["start"]), "chunk_id": chunk_id, "role": role, "clues": []})
    per_line = {}
    for m in mentions:
        per_line[m["line"]] = per_line.get(m["line"], 0) + 1
    for r in records:                                   # 식별자는 이름과 같은 줄, 그 줄에 이름이 1번뿐일 때만
        if r["label"] in CLUE_LABELS and not is_shared_number(r["label"], r["normalized"]) \
                and not in_narrative_cell(original, r["start"]):
            line = line_of(r["start"])
            if per_line.get(line) == 1:
                m = next(m for m in mentions if m["line"] == line)
                if r["token"] not in m["clues"]:
                    m["clues"].append(r["token"])
                m.setdefault("clue_kinds", {}).setdefault(r["label"], set()).add(r["token"])
                m.setdefault("clue_spans", []).append({"token": r["token"], "span": [r["start"], r["end"]]})

    # --- H2 / N6: 같은 이름 토큰의 언급을 묶거나 나눈다
    # 단서가 있는 언급을 먼저 묶는다. 단서 없는 언급이 문서 순서상 먼저 나와 묶음의 기준이 되면
    # 뒤에 나온 다른 사람이 그 묶음에 붙을 수 있다 (같은 문서의 동명이인이 한 행으로 섞임).
    clusters, apart = [], {}                             # apart: {N6/N5 로 갈라진 클러스터 번호 쌍: 규칙 번호}
    informative = [m for m in mentions if _has_clue(original, m, rules)]
    for m in informative + [m for m in mentions if m not in informative]:
        neutral = m not in informative
        joined, conflict, fits = None, {}, []
        for ci, members in enumerate(clusters):
            if members[0]["token"] != m["token"]:
                continue
            codes = [c for c in (_split_rule(original, x, m, rules) for x in members) if c]
            if codes:
                conflict[ci] = codes[0]
            else:
                fits.append(ci)
        if neutral and len(fits) > 1:                    # 누구인지 모름: 따로 두되 다른 사람으로 기록하지 않음
            clusters.append([m])
            continue
        if fits:
            joined = fits[0]
            clusters[joined].append(m)
        else:
            clusters.append([m])
            joined = len(clusters) - 1
        for ci, code in conflict.items():
            apart[frozenset((ci, joined))] = code
    # 행 순서는 문서에 처음 나온 위치 순으로 되돌린다
    for members in clusters:
        members.sort(key=lambda x: x["start"])
    order = sorted(range(len(clusters)), key=lambda i: clusters[i][0]["start"])
    new_index = {old: new for new, old in enumerate(order)}
    clusters = [clusters[i] for i in order]
    apart = {frozenset(new_index[i] for i in pair): code for pair, code in apart.items()}

    rows = []
    for members in clusters:
        known = [x["role"] for x in members if x["role"] != "unknown"]
        info = []
        for x in members:
            info.extend(t for t in x["clues"] if t not in info)
        chunk_ids = sorted({x["chunk_id"] for x in members}, key=lambda c: int(c.rsplit("#", 1)[1]))
        name_spans = [[x["start"], x["end"]] for x in members]
        clues = [c for x in members for c in x.get("clue_spans", [])]
        rows.append({"person_id": new_id("tmp_"), "doc_id": doc_id, "token": members[0]["token"],
                     "role": known[0] if known else "unknown", "info_label": info,
                     "evidence": new_evidence(chunk_ids, now, name_spans, clues), "reg_id": None,
                     "resolution_status": "pending", "created_at": now, "updated_at": now})
    for pair, code in apart.items():
        a, b = (rows[i] for i in sorted(pair))
        for x, y in ((a, b), (b, a)):
            x["evidence"]["not_same_as"][y["person_id"]] = code

    # --- M4 / N7 / R3: '이름만' 쓴 행 ↔ 같은 문서의 성명 행
    links = []
    norms = [clusters[i][0]["norm"] for i in range(len(rows))]
    depts = [next((d for d in (_dept(original, x) for x in members) if d), None) for members in clusters]
    affils = [{a[0] for a in (_affil(original, x) for x in members) if a} for members in clusters]
    judge = PersonResolver([], token_info={r["token"]: {"label": r["label"], "normalized": r["normalized"]}
                                           for r in records}, role_group=rules.role_group)
    for i, short in enumerate(rows):
        fulls = [j for j in range(len(rows)) if j != i and is_given_of(norms[i], norms[j])]
        if len(fulls) != 1:                              # 0개: 해당 없음, 2개 이상: R3 (hold)
            continue
        full = rows[fulls[0]]
        j = fulls[0]
        if judge.diff_rule(short, full) or _dept_differs(rules, short, full, depts[i], depts[j]) \
                or (len(affils[i]) == 1 and len(affils[j]) == 1 and affils[i] != affils[j]) \
                or _counterparts(original, clusters[i], clusters[j]):
            for x, y in ((short, full), (full, short)):  # N7: N1~N6 이 걸리면 그 후보와만 none
                x["evidence"]["not_same_as"][y["person_id"]] = "N7"
        else:
            links.append((short["person_id"], full["person_id"], "M4"))
            short['evidence'].setdefault('document_links', []).append(full['person_id'])
    return rows, links


def _split_rule(text, x, y, rules):
    """같은 문서의 같은 이름 언급 x(앞), y(뒤)가 별개 인물이라는 근거의 규칙 번호 (N5 | N6). 없으면 None."""
    gx, gy = rules.role_group.get(x["role"]), rules.role_group.get(y["role"])
    if gx not in (None, "unknown") and gy not in (None, "unknown") and gx != gy:
        return "N6"                                      # 같은 줄 역할 그룹이 다름
    dx, dy = _dept(text, x), _dept(text, y)
    if gx == gy == "staff" and dx and dy and dx != dy:
        return "N5"                                      # 사내 소속이 다름 (같은 문서)
    ax_, ay_ = _affil(text, x), _affil(text, y)
    if ax_ and ay_ and ax_[0] != ay_[0]:
        return "N5"                                      # 사내 부서 vs 외부 조직 (역할 미상이어도 적용)
    kx, ky = x.get("clue_kinds", {}), y.get("clue_kinds", {})
    if any(not kx[k] & ky[k] for k in kx.keys() & ky.keys()):
        return "N6"                                      # 같은 줄 번호가 다름 (같은 종류끼리만 비교: 전화 vs 카드는 해당 없음)
    tx, ty = _title(text, x, rules), _title(text, y, rules)
    if tx and ty and tx != ty:
        return "N6"                                      # 직급이 다름
    ax, ay = _ab(text, x), _ab(text, y)
    if ax and ay and ax != ay:
        return "N6"                                      # A/B 표기
    if _namesake(text, x) or _namesake(text, y):
        return "N6"                                      # '동명이인' 표기
    if x["line"] == y["line"] and _SUBJECT.match(text[x["end"]:x["end"] + 3]) \
            and _OBJECT.match(text[y["end"]:y["end"] + 3]):
        return "N6"                                      # 서로 상대하는 관계
    if _counterparts(text, [x], [y]):
        return "N6"                                      # 메일의 받는 사람 ↔ 보내는 사람
    return None


def _has_clue(text, m, rules):
    """다른 언급과 구분할 단서(역할·식별자·직급·소속·A/B 표기)가 있는 언급인가."""
    return bool(m["role"] != "unknown" or m["clues"] or _title(text, m, rules) or _ab(text, m)
                or _affil(text, m) or _is_addressee(text, m) or _is_signer(text, m))


def _affil(text, m):
    """이름 옆 소속: ('internal', 부서명) · ('external', 표시어) · None. 앞뒤 2어절(표는 같은 행)."""
    dept = _dept(text, m)
    if dept:
        return ("internal", dept)
    line_start = text.rfind("\n", 0, m["start"]) + 1
    line_end = text.find("\n", m["end"])
    line_end = len(text) if line_end < 0 else line_end
    words = text[line_start:m["start"]].split()[-2:] + text[m["end"]:line_end].split()[:2]
    for w in words:
        w = w.strip(" ,.;:()[]")
        if _EXTERNAL.match(w):
            return ("external", w)
    return None


def _is_addressee(text, m):
    return bool(_ADDRESSEE.match(text[m["end"]:m["end"] + 4]))


def _is_signer(text, m):
    line_end = text.find("\n", m["end"])
    return bool(_SIGNER.match(text[m["end"]:len(text) if line_end < 0 else line_end]))


def _counterparts(text, xs, ys):
    """한쪽은 받는 사람('○○ 씨,'), 다른 쪽은 보내는 사람('○○ 드림') 언급을 가졌는가."""
    return (any(_is_addressee(text, x) for x in xs) and any(_is_signer(text, y) for y in ys)) \
        or (any(_is_signer(text, x) for x in xs) and any(_is_addressee(text, y) for y in ys))


def _dept(text, m):
    """이름 바로 옆(같은 줄 앞뒤 2어절)에 적힌 부서. 없으면 None. 표의 행이면 그 줄 전체를 본다 (정의서 N5)."""
    line_start = text.rfind("\n", 0, m["start"]) + 1
    line_end = text.find("\n", m["end"])
    line_end = len(text) if line_end < 0 else line_end
    line = text[line_start:line_end]
    if line.lstrip().startswith("|"):
        words = line.replace("|", " ").split()
    else:
        words = text[line_start:m["start"]].split()[-2:] + text[m["end"]:line_end].split()[:2]
    for w in words:
        w = w.strip(" ,.;:()[]")
        if _DEPT.search(w):
            return w
    return None


def _dept_differs(rules, a, b, da, db):
    ga, gb = rules.role_group.get(a["role"]), rules.role_group.get(b["role"])
    return ga == gb == "staff" and bool(da and db and da != db)


def _title(text, m, rules):
    """이름 옆 2어절 안의 직급어 (대리·과장 …). 없으면 None."""
    line_start = text.rfind("\n", 0, m["start"]) + 1
    line_end = text.find("\n", m["end"])
    line_end = len(text) if line_end < 0 else line_end
    words = text[line_start:m["start"]].split()[-2:] + text[m["end"]:line_end].split()[:2]
    for w in words:
        for c in _candidates(w):
            for t in ((c, c[:-1]) if c.endswith("님") and len(c) > 1 else (c,)):   # '과장님', '책임님께'
                if rules.cue_to_role.get(t) == "employee":
                    return t
    return None


def _ab(text, m):
    hit = _AB.match(text[m["end"]:m["end"] + 6])
    return hit.group(1).upper() if hit else None


def _namesake(text, m):
    return "동명이인" in text[max(0, m["start"] - 30):m["end"] + 30]
