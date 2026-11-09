"""인물 사전: 문서 속 이름 언급을 '같은 사람' 단위로 묶는다 (인물사전 분석 및 설계 5·8장).

이 파일은 단계별로 채운다.
  3-1 이름 정규화 · 성/이름 나누기 · 이름 조회 키(name_hmac)
  3-2 식별자 붙이기 (같은 줄, 사람 1명일 때만)
  3-3 문서 안 묶기 (R1~R3)
  3-4 문서 간 연결 (R4~R8)
  3-5 토큰 만들기 (확정 = person_id 기준, 임시 = 이름 기준) · 저장할 행 만들기
"""
import hashlib
import hmac
import re
import uuid

from . import config, pii_pipeline
from .roles import role_for_mention

SURNAME2 = ("남궁", "황보", "제갈", "선우", "독고", "사공", "서문")   # 복성 (설계서 kkr 4장)


def normalize_name(text):
    """이름 표기 → 비교용 값. '김 민준', '김민준' → '김민준' (공백·점·하이픈 제거, NFC).

    가명처리와 같은 정규화 함수를 써야 이름 토큰과 같은 기준으로 묶인다.
    """
    return pii_pipeline.normalize_value("person", text)


def split_name(name):
    """정규화된 성명 → (성, 이름). 복성을 먼저 본다. '남궁나연' → ('남궁', '나연')

    성을 알 수 없는 짧은 이름(2글자 미만)이면 ('', name).
    """
    if len(name) >= 3 and name[:2] in SURNAME2:
        return name[:2], name[2:]
    if len(name) >= 2:
        return name[:1], name[1:]
    return "", name


def name_hmac(name):
    """이름 조회 키. HMAC(팀 키, 'alias:' + 정규화 이름) 16진수 64자리.

    - 원문 이름을 표에 남기지 않고 '같은 이름인지'만 비교하려고 쓴다 (vault.person_alias, rag.person_lookup).
    - 'alias:' 를 붙여 가명 토큰(HMAC('person|' + 이름))과 값이 겹치지 않게 한다 (용도 분리).
    """
    if not name:
        raise ValueError("이름 조회 키를 만들 이름이 비어 있음")
    return hmac.new(pii_pipeline._key(), f"alias:{name}".encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- 3-2 식별자 붙이기
# 동일인 근거로 쓰는 강한 식별자 4종 (설계서 8.3). 주소·카드번호는 쓰지 않는다.
STRONG_LABELS = ("resident_registration_number", "phone_number", "account_number", "email")
# 항목명이 이 말로 끝나면 서술 칸 → 그 안의 번호는 다른 사람 번호일 수 있어 붙이지 않는다.
NARRATIVE_LABELS = ("내용", "메모", "요약", "비고", "특이사항", "경위", "사유", "결과", "의견", "조치", "안내", "처리")
_SEGMENT_SEP = re.compile(r"\s/\s|\||;")          # 한 줄 안의 칸 구분: ' / ', '|', ';'
_FIELD = re.compile(r"^\s*([^:：]{1,20}?)\s*[:：]\s*")


def _line_bounds(text, pos):
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return start, len(text) if end < 0 else end


def _segment_start(text, line_start, pos):
    """pos가 들어 있는 칸의 시작 위치 (같은 줄의 마지막 구분자 뒤)."""
    starts = [line_start] + [line_start + m.end() for m in _SEGMENT_SEP.finditer(text[line_start:pos])]
    return max(starts)


def _identifier_context_ok(text, ident, line_start, person_spans):
    """식별자가 '이 사람의 정보'라고 적힌 자리인지 (설계서 8.3 수집 규칙).

    붙이는 자리: ① 표의 행 ② '항목: 값' 칸의 값 (서술 칸 제외, 값 앞에 한 단어까지 허용: '가상은행 123-…')
                ③ 값만 있는 칸 ('신청인: 김민준 / 010-…')
    붙이지 않는 자리: 문장 속 번호, 내용·메모 같은 서술 칸.
    """
    if text[line_start:ident["start"]].lstrip().startswith("|"):
        return True                                                    # ① 표의 행
    seg = _segment_start(text, line_start, ident["start"])
    before = text[seg:ident["start"]]
    field = _FIELD.match(before)
    if field:
        label = field.group(1).strip()
        if label.endswith(NARRATIVE_LABELS):
            return False                                               # 서술 칸
        return len(before[field.end():].split()) <= 1                  # ② 항목 값
    return not before.strip()                                          # ③ 값만 있는 칸


def attach_identifiers(text, spans):
    """같은 줄의 강한 식별자를 그 줄의 사람에게 붙인다.

    반환: {(이름 start, end): [식별자 토큰, ...]}  (토큰은 vault.token_map 과 같은 값)
    - 이름 바로 뒤 괄호 '김민준(010-…)' 은 그 이름에 붙인다 (줄에 사람이 여러 명이어도).
    - 그 밖에는 줄에 서로 다른 이름이 정확히 1명이고, 식별자가 정보 칸에 있을 때만 붙인다.
    """
    persons = [s for s in spans if s["label"] == "person"]
    out = {}
    for ident in (s for s in spans if s["label"] in STRONG_LABELS):
        try:
            token = pii_pipeline.make_token(
                ident["label"], pii_pipeline.normalize_value(ident["label"], text[ident["start"]:ident["end"]]))
        except ValueError:
            continue                                                   # 마스킹된 번호 등은 비교 불가
        line_start, line_end = _line_bounds(text, ident["start"])
        owner = next((p for p in persons
                      if text[p["end"]:ident["start"]].strip() == "("), None)       # 괄호
        if owner is None:
            on_line = [p for p in persons if line_start <= p["start"] < line_end]
            names = set()
            for p in on_line:
                try:
                    names.add(normalize_name(text[p["start"]:p["end"]]))
                except ValueError:
                    pass
            if len(names) != 1 or not _identifier_context_ok(text, ident, line_start, persons):
                continue
            owner = on_line[0]
        out.setdefault((owner["start"], owner["end"]), [])
        if token not in out[(owner["start"], owner["end"])]:
            out[(owner["start"], owner["end"])].append(token)
    return out


# ---------------------------------------------------------------- 3-3 문서 안 묶기 (R1~R3)
def _mentions(text, spans, rules):
    """이름 언급마다 정규화 이름·역할 그룹·붙은 식별자를 모은다."""
    persons = [s for s in spans if s["label"] == "person"]
    ids = attach_identifiers(text, spans)
    out = []
    for p in persons:
        try:
            name = normalize_name(text[p["start"]:p["end"]])
        except ValueError:
            continue
        others = [o for o in persons if o is not p]
        role, _ = role_for_mention(text, p["start"], p["end"], rules, others)
        out.append({"start": p["start"], "end": p["end"], "name": name, "role": role,
                    "group": None if role == "unknown" else rules.role_group.get(role),
                    "ids": ids.get((p["start"], p["end"]), [])})
    return out


def _new_entity(name, kind, rule):
    return {"name": name, "kind": kind, "mentions": [], "groups": set(), "roles": set(), "ids": [],
            "rules": [rule]}


def _add(entity, m):
    entity["mentions"].append((m["start"], m["end"]))
    if m["group"]:
        entity["groups"].add(m["group"])
        entity["roles"].add(m["role"])
    for t in m["ids"]:
        if t not in entity["ids"]:
            entity["ids"].append(t)


def _group_same_name(ms, kind):
    """R1: 이름 키가 같은 언급을 한 개체로. 단, 규칙으로 정한 역할 그룹이 둘 이상이면 그룹마다 나누고,
    역할을 모르는 언급은 어느 쪽인지 알 수 없으니 따로 둔다 (애매하면 분리)."""
    by_name = {}
    for m in ms:
        by_name.setdefault(m["name"], []).append(m)
    entities = []
    for name, group_ms in by_name.items():
        groups = {m["group"] for m in group_ms if m["group"]}
        if len(groups) <= 1:
            e = _new_entity(name, kind, "R1")
            for m in group_ms:
                _add(e, m)
            entities.append(e)
            continue
        for g in sorted(groups):
            e = _new_entity(name, kind, "R1-split")
            for m in group_ms:
                if m["group"] == g:
                    _add(e, m)
            entities.append(e)
        for m in group_ms:
            if not m["group"]:
                e = _new_entity(name, kind, "R1-split")
                _add(e, m)
                entities.append(e)
    return entities


def group_mentions(text, spans, rules):
    """한 문서 안의 이름 언급을 '문서 내 개체'로 묶는다 (설계서 8.1).

    R1 이름 키가 같은 성명 언급 → 한 개체
    R2 이름만 쓴 언급('민준') → 이름 부분이 같은 성명 개체('김민준')에 연결. 역할 그룹이 충돌하면 연결 안 함
    R3 이름 부분이 같은 성명 개체가 둘 이상('김민준', '이민준') → 연결 안 함 (이름만 쓴 개체로 남김)

    반환: [{"name", "kind": full|given, "mentions": [(start, end)], "groups": {역할 그룹},
            "ids": [식별자 토큰], "rules": [적용 규칙]}]
    """
    ms = _mentions(text, spans, rules)
    names = {m["name"] for m in ms}
    given_names = {n for n in names
                   if any(other != n and split_name(other)[1] == n for other in names)}
    full = _group_same_name([m for m in ms if m["name"] not in given_names], "full")
    leftover = []
    for m in (m for m in ms if m["name"] in given_names):
        cands = [e for e in full if split_name(e["name"])[1] == m["name"]]
        if len({e["name"] for e in cands}) != 1:
            leftover.append(m)                                         # R3: 성명 후보가 둘 이상
            continue
        ok = [e for e in cands if not m["group"] or not e["groups"] or m["group"] in e["groups"]]
        if len(ok) != 1:
            leftover.append(m)                                         # R2 예외: 역할 충돌 (또는 나뉜 개체 중 모호)
            continue
        _add(ok[0], m)
        if "R2" not in ok[0]["rules"]:
            ok[0]["rules"].append("R2")
    return full + _group_same_name(leftover, "given")


# ---------------------------------------------------------------- 3-4 문서 간 연결 (R4~R8)
RRN_PREFIX = "<RESIDENT_REGISTRATION_NUMBER_"


def _switch(name, default="off"):
    """PERSON_LINK_UNIQUE (R6, 기본 on) / PERSON_LINK_GIVEN (R7, 기본 off) = on|off."""
    mode = (config.env(name, default) or default).strip().lower()
    if mode not in ("on", "off"):
        raise ValueError(f"{name} 는 on 또는 off 여야 합니다: {mode}")
    return mode == "on"


def _shared_across_names(doc_entities):
    """서로 다른 이름의 개체에 함께 붙은 식별자 → 연결 근거로 쓰지 않는다 (가족·대리인 공유, linkable=false)."""
    names = {}
    for _, e in doc_entities:
        for t in e["ids"]:
            names.setdefault(t, set()).add(e["name"])
    return {t for t, ns in names.items() if len(ns) > 1}


def _conflict(a, b):
    """R5: 역할 그룹이 서로 겹치지 않거나, 주민등록번호가 서로 다르면 같은 사람이 아니다."""
    if a["groups"] and b["groups"] and not (a["groups"] & b["groups"]):
        return True
    ra = {t for t in a["ids"] if t.startswith(RRN_PREFIX)}
    rb = {t for t in b["ids"] if t.startswith(RRN_PREFIX)}
    return bool(ra and rb and ra.isdisjoint(rb))


def _join(into, c, rule):
    into["members"] += c["members"]
    into["ids"] += [t for t in c["ids"] if t not in into["ids"]]
    into["groups"] |= c["groups"]
    into["rules"] = sorted(set(into["rules"] + c["rules"] + [rule]))


def _merge_confirmed(confirmed):
    """R6: 이름이 같은 확정 인물끼리도 충돌 증거가 없으면 한 사람으로 둔다.

    한 사람이 사무실·휴대폰처럼 번호를 여러 개 쓰면 R4 로는 이어지지 않는다.
    지금(이름 토큰)은 이들이 한 토큰이므로, 충돌 증거가 없는데 나누면 같은 사람만 쪼개진다.
    - 역할 그룹이 같은 확정 인물끼리 합친다 (주민번호가 다르면 합치지 않음).
    - 역할을 모르는 확정 인물은 붙일 곳이 정확히 1곳일 때만 합친다. 2곳 이상이면 따로 둔다.
    """
    keyed, unkeyed = [], []
    for c in confirmed:
        if not c["groups"]:
            unkeyed.append(c)
            continue
        same = [k for k in keyed if k["groups"] == c["groups"] and not _conflict(k, c)]
        if len(same) == 1:
            _join(same[0], c, "R6")
        else:
            keyed.append(c)
    rest = []
    for c in unkeyed:
        fits = [k for k in (keyed or rest) if not _conflict(k, c)]
        if len(fits) == 1:
            _join(fits[0], c, "R6")
        else:
            rest.append(c)
    return keyed + rest


def _resolve_loose(comps):
    """R6: 식별자 없는 개체를, 붙일 확정 인물이 분명할 때만 그 인물에 합친다.

    1) 식별자 없는 개체를 역할 그룹별로 모은다 (고객끼리, 사내끼리, 역할 모름끼리).
    2) 역할 그룹이 있는 묶음: 충돌하지 않는 확정 인물이 정확히 1명이면 합친다.
    3) 역할을 모르는 묶음: 이 이름의 확정 인물이 정확히 1명이면 합친다.
    4) 붙일 곳이 없거나(0명) 애매하면(2명 이상) 이름별 임시 인물 하나에 모은다
       → 지금처럼 이름 기준 토큰을 유지한다 (설계서 10장: 독립 ID를 주면 같은 사람이 또 쪼개진다).
    반환: (확정 인물 묶음 목록, 임시 인물 묶음 또는 None)
    """
    confirmed = _merge_confirmed([c for c in comps if c["ids"]])
    buckets = {}
    for c in comps:
        if c["ids"]:
            continue
        key = next(iter(c["groups"])) if len(c["groups"]) == 1 else None
        buckets.setdefault(key, []).append(c)
    held = []
    for key, cs in buckets.items():
        bucket = {"members": [i for c in cs for i in c["members"]],
                  "ids": [], "groups": set().union(*(c["groups"] for c in cs)), "rules": []}
        fits = [c for c in confirmed if not _conflict(c, bucket)] if key else confirmed
        if len(fits) == 1:
            _join(fits[0], bucket, "R6")
        else:
            held.append(bucket)
    provisional = None
    if held:
        provisional = {"members": [i for b in held for i in b["members"]], "ids": [],
                       "groups": set().union(*(b["groups"] for b in held)), "rules": []}
    return confirmed, provisional


def link_documents(doc_entities, new_id=lambda: str(uuid.uuid4())):
    """문서 안 개체들을 문서 간에 잇는다 (설계서 8.2). 같은 이름 키끼리만 본다.

    doc_entities: [(doc_id, 문서 안 개체), ...]  (group_mentions 결과를 문서별로 모은 것)
    반환: {
      "persons":  [{"person_id", "status": confirmed|provisional, "name", "kind",
                    "members": [doc_entities 의 번호], "ids": [식별자 토큰], "rules": [...]}],
      "unlinkable": {공유 식별자 토큰},
    }
    R4 같은 이름 + 같은 강한 식별자(공유 식별자 제외) → 한 사람 (확정)
    R5 역할 그룹 충돌·주민번호 불일치 → 합치지 않음
    R6 (PERSON_LINK_UNIQUE, 기본 on) 근거 없는 개체도 붙일 확정 인물이 정확히 1명이면 합침
    R7 (PERSON_LINK_GIVEN, 기본 off) 이름만 쓴 사내 인물 → 이름 부분이 같은 사내 성명 인물이 정확히 1명이면 연결
    R8 그 밖의 이름만 쓴 개체는 문서 간 연결하지 않음
    근거가 없는 개체는 이름별로 '임시 인물' 하나에 모인다 (지금처럼 이름 기준 토큰 = 같은 토큰).
    """
    unlinkable = _shared_across_names(doc_entities)
    by_name = {}
    for i, (_, e) in enumerate(doc_entities):
        by_name.setdefault((e["kind"], e["name"]), []).append(i)

    persons = []
    for (kind, name), members in sorted(by_name.items()):
        comps = []                                   # [{"members", "ids", "groups", "rules"}]
        for i in members:
            e = doc_entities[i][1]
            ids = [t for t in e["ids"] if t not in unlinkable]
            comps.append({"members": [i], "ids": list(ids), "groups": set(e["groups"]), "rules": []})
        if kind == "full":                           # R4: 식별자를 공유하는 개체끼리 합친다 (충돌하면 합치지 않음)
            merged = True
            while merged:
                merged = False
                for a in range(len(comps)):
                    for b in range(a + 1, len(comps)):
                        A, B = comps[a], comps[b]
                        if set(A["ids"]) & set(B["ids"]) and not _conflict(A, B):
                            _join(A, B, "R4")
                            del comps[b]
                            merged = True
                            break
                    if merged:
                        break
        confirmed = [c for c in comps if c["ids"]]
        loose = [c for c in comps if not c["ids"]]
        provisional = None
        if loose:                                    # 근거 없음 → 이름별 임시 인물 하나 (이름 토큰 공유)
            provisional = {"members": [i for c in loose for i in c["members"]], "ids": [],
                           "groups": set().union(*(c["groups"] for c in loose)), "rules": []}
        if kind == "full" and _switch("PERSON_LINK_UNIQUE", "on"):     # R6
            confirmed, provisional = _resolve_loose(comps)
        for c in confirmed:
            persons.append({"person_id": new_id(), "status": "confirmed", "name": name, "kind": kind,
                            "members": sorted(c["members"]), "ids": c["ids"],
                            "groups": c["groups"], "rules": c["rules"] or ["R4"]})
        if provisional:
            persons.append({"person_id": new_id(), "status": "provisional", "name": name, "kind": kind,
                            "members": sorted(provisional["members"]), "ids": [],
                            "groups": provisional["groups"], "rules": []})

    if _switch("PERSON_LINK_GIVEN"):                 # R7: 이름만 쓴 사내 인물
        staff_full = [p for p in persons if p["kind"] == "full" and "staff" in p["groups"]]
        for p in [p for p in persons if p["kind"] == "given" and p["groups"] == {"staff"}]:
            cands = [f for f in staff_full if split_name(f["name"])[1] == p["name"]]
            if len(cands) == 1:
                cands[0]["members"] = sorted(cands[0]["members"] + p["members"])
                cands[0]["rules"] = sorted(set(cands[0]["rules"] + ["R7"]))
                persons.remove(p)
    return {"persons": persons, "unlinkable": unlinkable}


# ---------------------------------------------------------------- 3-5 토큰 만들기 · 저장할 행 만들기
def person_token(person):
    """확정 인물 = HMAC("person:" + person_id), 임시 인물 = 지금과 같은 이름 토큰.

    확정 토큰은 이름에서 계산하지 않으므로 동명이인이라도 사람이 다르면 토큰이 다르다.
    임시 토큰은 make_token("person", 이름) 그대로라 인물 사전을 꺼도 켜도 같은 값이다.
    """
    if person["status"] == "confirmed":
        digest = hmac.new(pii_pipeline._key(), f"person:{person['person_id']}".encode(),
                          hashlib.sha256).hexdigest()
        return f"<PERSON_{digest[:pii_pipeline.TOKEN_HEX]}>"
    return pii_pipeline.make_token("person", person["name"])


def gender_from_rrn(normalized):
    """주민번호(숫자 13자리) 7번째 자리: 홀수 남(M), 짝수 여(F). 형식이 다르면 None."""
    if not normalized or len(normalized) != 13 or not normalized.isdigit():
        return None
    return "M" if int(normalized[6]) % 2 == 1 else "F"


def build_dictionary(docs, rules, id_values=None, new_id=lambda: str(uuid.uuid4())):
    """문서들로 인물 사전을 만든다 (3-3 문서 안 묶기 → 3-4 문서 간 연결 → 3-5 토큰).

    docs: [{"id", "text", "spans": [{"label", "start", "end"}]}]   (탐지 결과 span)
    id_values: {식별자 토큰: 정규화 값}  (성별 계산용. 주민번호 값만 쓴다)
    반환: {
      "persons":  [{"person_id", "status", "name", "kind", "token", "gender", "rules",
                    "doc_ids", "roles", "surfaces", "ids", "id_docs", "shared_ids", "mentions"}],
      "mentions": {(doc_id, start, end): {"token", "person_id", "name"}},
      "unlinkable": {공유 식별자 토큰},
    }
    """
    id_values = id_values or {}
    doc_entities, texts, order = [], {}, {}
    for d in docs:
        texts[d["id"]] = d["text"]
        person_spans = sorted((s for s in d["spans"] if s["label"] == "person"), key=lambda s: s["start"])
        order[d["id"]] = {(s["start"], s["end"]): i for i, s in enumerate(person_spans)}
        for e in group_mentions(d["text"], d["spans"], rules):
            doc_entities.append((d["id"], e))
    linked = link_documents(doc_entities, new_id)

    persons, mentions = [], {}
    for p in linked["persons"]:
        token = person_token(p)
        doc_ids, roles, surfaces, all_ids, id_docs, ments = set(), set(), set(), set(), {}, []
        for i in p["members"]:
            doc_id, e = doc_entities[i]
            doc_ids.add(doc_id)
            roles |= e["roles"]
            for t in e["ids"]:
                all_ids.add(t)
                id_docs.setdefault(t, doc_id)
            for start, end in e["mentions"]:
                surfaces.add(normalize_name(texts[doc_id][start:end]))
                mentions[(doc_id, start, end)] = {"token": token, "person_id": p["person_id"], "name": p["name"]}
                ments.append({"doc_id": doc_id, "mention": order[doc_id][(start, end)]})
        genders = {gender_from_rrn(id_values.get(t)) for t in p["ids"] if t.startswith(RRN_PREFIX)} - {None}
        persons.append({
            "person_id": p["person_id"], "status": p["status"], "name": p["name"], "kind": p["kind"],
            "token": token, "gender": genders.pop() if len(genders) == 1 else None,
            "rules": p["rules"], "doc_ids": sorted(doc_ids), "roles": sorted(roles),
            "surfaces": sorted(surfaces), "ids": list(p["ids"]), "id_docs": id_docs,
            "shared_ids": sorted(all_ids & linked["unlinkable"]),
            "mentions": sorted(ments, key=lambda m: (m["doc_id"], m["mention"])),
        })
    return {"persons": persons, "mentions": mentions, "unlinkable": linked["unlinkable"]}


def retokenize(masked, records, mentions, doc_id):
    """가명 텍스트의 이름 토큰을 인물 사전 토큰으로 바꾼다.

    - 토큰 길이가 같아도 위치를 믿지 않고, 가명 텍스트의 토큰 순서 = records 순서인지 먼저 확인한다.
    - 바뀐 record 는 normalized 를 인물 이름으로 맞춘다 (한 토큰 = 한 정규화 값, token_map 규칙).
    - '도윤'처럼 인물 이름과 표기가 다르면 원래 이름 토큰을 alias_token 으로 남겨 짧은 이름 검색도 된다.
    반환: (새 가명 텍스트, 새 records)
    """
    found = list(pii_pipeline.TOKEN_PATTERN.finditer(masked))
    if [m.group() for m in found] != [r["token"] for r in records]:
        raise ValueError("가명 텍스트의 토큰 순서와 records가 다름")
    out, new_records, last = [], [], 0
    for m, r in zip(found, records):
        hit = mentions.get((doc_id, r["start"], r["end"])) if r["label"] == "person" else None
        out.append(masked[last:m.start()])
        last = m.end()
        if not hit:
            out.append(r["token"])
            new_records.append(r)
            continue
        out.append(hit["token"])
        r2 = {k: v for k, v in r.items() if k != "alias_token"}
        r2.update(token=hit["token"], normalized=hit["name"], person_id=hit["person_id"])
        surface = normalize_name(r["original"])
        if surface != hit["name"]:
            r2["alias_token"] = pii_pipeline.make_token("person", surface)
        new_records.append(r2)
    out.append(masked[last:])
    return "".join(out), new_records


def vault_rows(dictionary):
    """vault 4개 표에 넣을 행. 원문 이름 없이 HMAC·토큰·UUID 만 담는다."""
    registry, aliases, identifiers, logs = [], {}, [], []
    for p in dictionary["persons"]:
        pid = p["person_id"]
        registry.append({"person_id": pid, "status": p["status"], "gender": p["gender"]})
        rule = ",".join(p["rules"]) or None
        for surface in p["surfaces"]:
            kind = "full" if p["kind"] == "full" and surface == p["name"] else "given"
            aliases.setdefault((name_hmac(surface), pid, kind),
                               {"name_hmac": name_hmac(surface), "person_id": pid, "kind": kind,
                                "rule_id": rule if surface == p["name"] else "R2"})
        for t in p["ids"]:
            identifiers.append({"id_token": t, "person_id": pid, "first_doc_id": p["id_docs"][t], "linkable": True})
        for t in p["shared_ids"]:
            identifiers.append({"id_token": t, "person_id": pid, "first_doc_id": p["id_docs"][t], "linkable": False})
        if p["status"] == "confirmed":                  # 임시(이름 토큰) → 확정 토큰. 되돌릴 때 이 기록을 쓴다
            logs.append({"action": "promote", "to_person_id": pid,
                         "from_token": pii_pipeline.make_token("person", p["name"]), "to_token": p["token"],
                         "mentions": p["mentions"], "rule_id": rule})
    return {"registry": registry, "aliases": list(aliases.values()), "identifiers": identifiers, "logs": logs}


def lookup_rows(dictionary):
    """rag.person_lookup 행: 이름 HMAC → 후보 토큰. 원문·식별자·person_id 는 넣지 않는다."""
    rows = {}
    for p in dictionary["persons"]:
        for surface in p["surfaces"]:
            key = (name_hmac(surface), p["token"])
            row = rows.setdefault(key, {"name_hmac": key[0], "token": key[1], "status": p["status"],
                                        "roles": set(), "docs": set()})
            if p["status"] == "confirmed":
                row["status"] = "confirmed"
            row["roles"] |= set(p["roles"])
            row["docs"] |= set(p["doc_ids"])
    return [{"name_hmac": r["name_hmac"], "token": r["token"], "status": r["status"],
             "roles": sorted(r["roles"]), "doc_count": len(r["docs"])} for r in rows.values()]
