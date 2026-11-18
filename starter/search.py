"""질의 토큰과 의미 검색을 함께 쓰는 검색.

    python -m starter.search "보이스피싱 피해 접수 기록 찾아줘"
    SEARCH_MODE=boost python -m starter.run_queries --out outputs/run_boost.jsonl   # 비교 실험

검색 방식 (.env 또는 환경변수 SEARCH_MODE, 기본 and)
  and   : 질의 토큰을 '모두' 포함한 문서만 후보로 삼고 그 안에서 의미 검색 (팀 기본안)
          토큰이 서로 다른 청크에 있어도 같은 문서이면 후보. 일치 후보가 없으면 빈 결과.
  boost : 의미 검색 후보 + 질의 토큰을 '하나라도' 가진 문서 후보를 모은 뒤
          점수 = 의미 유사도 + TOKEN_WEIGHT × (질의 토큰 중 문서가 가진 비율)
          질의 탐지가 하나 틀려도 정답이 사라지지 않는다. (비교 실험용)
  토큰 없는 질의는 두 방식 모두 전체 의미 검색.

임베딩에는 pii_pipeline.embedding_text()로 토큰을 라벨 이름으로 바꾼 질의를 넣는다
(문서도 ingest에서 같은 처리를 하므로 둘이 같은 공간에서 비교된다).

다른 코드(run_queries.py, chat.py)가 쓰는 약속
  search(query, k, conn=None) -> (masked_query, query_tokens, docs)
  search(query, k, conn=None, with_meta=True) -> (masked_query, query_tokens, docs, meta)
  docs: [{"doc_id", "chunk_id", "masked_text", "tokens", "score"}, ...]  순위 순서
  meta: {"regions", "ask_region", "roles", "excluded", "person_candidates"}
검색 단계는 rag_user 계정만 씁니다 (매핑 테이블 접근 없음).

메타데이터 (종합, 기본 off. 켜도 SEARCH_MODE=and 경로에만 적용)
  REGION_META=on : 질문의 시도·시군구로 문서를 거른다 (REG-30~35). 이름이 겹치는 시군구는 되묻기.
  ROLE_META=on   : 질문의 역할 단서로 '규칙상 역할이 다른 문서'만 뺀다 (ROLE-40~46)
                   역할 조건은 질문 속 이름에 직접 붙은 역할어('직원 김민준', '김민준 고객')로만 정한다.
                   사람 없이 지역이 있는 질문('대구 사는 고객')은 질문 전체의 역할어를 쓴다.
                   남은 문서의 순서는 기존 점수 그대로 (등급 1·2로 순서를 바꾸면 같은 사람의
                   다른 문서가 올라와 이름 질문 점수가 떨어졌다: holdout 6문항)

인물 사전 (PERSON_DICT=on)
  이름·역할 검색은 rag.look_up에서 미확정 후보도 포함한다 (설계서 7.4, 8.1).
  특정 개인의 전체 문서는 명시적인 reg_id로 검색한다. 이름만으로 범위를 확장하지 않는다.
  후보가 여러 명이어도 검색 결과는 유지하며, 동일인으로 단정하는 답변은 보류한다.
  등록된 청크가 없으면 빈 결과를 반환한다. vault는 조회하지 않는다.

"""
import argparse
import re

from . import address, config, db, pii_pipeline
from .embed import embed
from .roles import load_rules, query_name_roles, query_roles

TOKEN_POOL = 200   # boost: 토큰 후보 최대 청크 수
ROLE_POOL = 60     # 역할 등급을 매길 후보 청크 수 (등급은 k개로 자르기 전에 매긴다)
_rules_cache = {}


def mask_query(query, with_records=False):
    # 질의는 strict=False: 처리할 수 없는 표기가 있어도 챗봇이 멈추지 않고 그 값만 [개인정보]로 가린다
    # with_records=True: 인물 사전 조회에 쓸 이름 정규화 값(records)도 돌려준다 (DB 로는 HMAC 만 보냄)
    masked, records = pii_pipeline.pseudonymize(query, pii_pipeline.detect(query), strict=False)
    tokens = sorted({r["token"] for r in records})
    return (masked, tokens, records) if with_records else (masked, tokens)


def person_candidates(cur, records, roles=(), meta=None):
    """이름 토큰 → 검색 가능한 chunk_id 목록. 빈 후보는 이름 토큰 검색으로 대체하지 않는다."""
    out = {}
    for r in records:
        token = r['token']
        if r['label'] != 'person' or token in out:
            continue
        cur.execute("""SELECT DISTINCT COALESCE(reg_id, person_id), chunk_id
            FROM rag.look_up
            WHERE token = %s AND (%s::text[] = '{}' OR role = ANY(%s::text[]))
            ORDER BY 1, 2""", (token, list(roles), list(roles)))
        rows = cur.fetchall()
        identities = {row[0] for row in rows}
        if meta is not None:
            meta['person_candidates'][token] = len(identities)
            if len(identities) > 1:
                meta['ask_person'] = True
        out[token] = sorted({row[1] for row in rows})
    return out


def token_conditions(q_tokens, candidates):
    """인물은 연결표에서 얻은 실제 청크, 그 외 토큰은 같은 문서 내 존재로 제한한다."""
    if not candidates:
        return ["""NOT EXISTS (
            SELECT 1 FROM unnest(%s::text[]) AS q(token)
            WHERE NOT EXISTS (SELECT 1 FROM rag.chunks AS matched
                WHERE matched.doc_id = chunks.doc_id AND matched.tokens @> ARRAY[q.token])
        )"""], [list(q_tokens)]
    where, params = [], []
    for token in q_tokens:
        if token in candidates:
            where.append('chunks.chunk_id = ANY(%s::text[])')
            params.append(candidates[token])
        else:
            where.append("""EXISTS (SELECT 1 FROM rag.chunks AS matched
                         WHERE matched.doc_id = chunks.doc_id AND matched.tokens @> %s::text[])""")
            params.append([token])
    return where, params


def _mode():
    mode = (config.env("SEARCH_MODE", "and") or "and").strip().lower()
    if mode not in ("and", "boost"):
        raise ValueError(f"SEARCH_MODE 는 and 또는 boost 여야 합니다: {mode}")
    return mode


def _rules(cur):
    if "rules" not in _rules_cache:
        _rules_cache["rules"] = load_rules(cur)
    return _rules_cache["rules"]


def role_grade(entries, wanted, groups):
    """문서 속 대상 인물 언급들(entries)의 역할 → 1 일치 / 2 미상 / 3 제외 (ROLE-41~44).

    wanted 가 비면 역할 조건 없음(1). 제외(3)는 모든 언급이 규칙(rule)으로 정해졌고
    질문 역할과 그룹까지 다를 때만. LLM 판정·unknown·같은 그룹(직원↔상담사)은 2로 남긴다.
    """
    if not wanted:
        return 1
    if not entries:
        return 2
    if {p["role"] for p in entries} & wanted:
        return 1
    wanted_groups = {groups.get(r) for r in wanted}
    if any(p["role"] == "unknown" or p.get("source") != "rule" or groups.get(p["role"]) in wanted_groups
           for p in entries):
        return 2
    return 3


def _doc_persons(cur, doc_ids):
    cur.execute("SELECT doc_id, persons FROM rag.chunks WHERE doc_id = ANY(%s)", (doc_ids,))
    out = {}
    for doc_id, persons in cur.fetchall():
        out.setdefault(doc_id, []).extend(persons or [])
    return out


def search(query, k=10, conn=None, with_meta=False, reg_id=None):
    dict_on = config.meta_on("PERSON_DICT")
    if reg_id and not dict_on:
        raise ValueError("reg_id 검색은 PERSON_DICT=on에서 사용하세요.")
    if dict_on:
        masked_q, q_tokens, q_records = mask_query(query, with_records=True)
    else:
        masked_q, q_tokens = mask_query(query)
        q_records = []
    meta = {"regions": [], "ask_region": None, "roles": [], "excluded": 0, "person_candidates": {}, "ask_person": False, "person_scope": "registry" if reg_id else "name_role"}
    if config.meta_on("REGION_META"):
        meta["regions"], meta["ask_region"] = address.query_regions(query)
        if meta["ask_region"]:                  # '중구 고객' → 검색하지 않고 되묻기 (REG-33)
            return (masked_q, q_tokens, [], meta) if with_meta else (masked_q, q_tokens, [])
    regions = meta["regions"]
    person_tokens = [t for t in q_tokens if t.startswith("<PERSON_")]
    vec = embed([pii_pipeline.embedding_text(masked_q)], kind="query")[0]
    mode = _mode()
    if dict_on and mode == "boost":
        raise ValueError("PERSON_DICT=on은 인물 조건을 유지하기 위해 SEARCH_MODE=and만 지원합니다.")
    own = conn is None
    conn = conn or db.rag_conn()
    try:
        with conn.cursor() as cur:
            if mode == "boost" and q_tokens:
                docs = rerank_top(masked_q, _search_boost(cur, vec, q_tokens, k))
                return (masked_q, q_tokens, docs, meta) if with_meta else (masked_q, q_tokens, docs)
            wanted, groups = set(), {}
            if (dict_on or config.meta_on("ROLE_META")) and (len(person_tokens) == 1 or (not person_tokens and regions)):
                rules = _rules(cur)
                groups = rules.role_group
                wanted = (query_name_roles(masked_q, person_tokens[0], rules) if person_tokens
                          else query_roles(query, rules))
            meta["roles"] = sorted(wanted)
            candidate_roles = {role for role, group in groups.items()
                               if role in wanted or (group and group != 'unknown' and group in {groups.get(w) for w in wanted})}
            selected_chunks = None
            if reg_id:
                # 7.4: 명시적으로 선택한 확정 인물은 문서별 역할과 무관하게 조회한다.
                if len(person_tokens) > 1:
                    raise ValueError("reg_id 검색에는 이름을 하나만 지정하세요.")
                cur.execute("SELECT DISTINCT chunk_id FROM rag.look_up WHERE reg_id = %s", (reg_id,))
                selected_chunks = sorted({row[0] for row in cur.fetchall()})
                candidates = {token: selected_chunks for token in person_tokens}
                meta['person_candidates'] = {token: int(bool(selected_chunks)) for token in person_tokens}
                if not selected_chunks:
                    return (masked_q, q_tokens, [], meta) if with_meta else (masked_q, q_tokens, [])
            else:
                candidates = person_candidates(cur, q_records, candidate_roles, meta) if dict_on else {}
            # 후보가 없을 때만 종료한다. 여러 후보의 검색 결과는 유지하고 답변 단계에서 보류한다.
            if dict_on and any(not c for c in candidates.values()):
                return (masked_q, q_tokens, [], meta) if with_meta else (masked_q, q_tokens, [])
            allowed = set(person_tokens)
            pool = max(k * 3, ROLE_POOL) if wanted else k * 3
            where, params = [], [vec]
            if selected_chunks is not None:
                where.append('chunks.chunk_id = ANY(%s::text[])')
                params.append(selected_chunks)
            if q_tokens:
                # 토큰 후보를 먼저 확정(MATERIALIZED)한 뒤 그 안에서만 거리 순으로 정렬한다.
                # 필터를 바깥 쿼리에 두면 k가 작을 때 DB가 HNSW 근사 인덱스를 골라
                # '가까운 약 40개'만 꺼낸 뒤 필터를 걸어, 정답이 그 밖에 있으면 빈 결과가 된다.
                token_where, token_params = token_conditions(q_tokens, candidates)
                where += token_where
                params += token_params
            if regions:
                # 지역도 토큰처럼 '같은 문서의 어느 청크든' 가지고 있으면 통과 (REG-30·31)
                where.append("""EXISTS (
                        SELECT 1 FROM rag.chunks AS region
                        WHERE region.doc_id = chunks.doc_id AND region.regions @> %s::text[]
                    )""")
                params.append(regions)
            if where:
                sql = f"""WITH cand AS MATERIALIZED (
                    SELECT doc_id, chunk_id, masked_text, tokens, embedding <=> %s AS dist
                    FROM rag.chunks AS chunks
                    WHERE {" AND ".join(where)}
                )
                SELECT doc_id, chunk_id, masked_text, tokens, dist FROM cand ORDER BY dist LIMIT %s"""
            else:
                sql = """SELECT doc_id, chunk_id, masked_text, tokens, embedding <=> %s AS dist
                         FROM rag.chunks AS chunks ORDER BY dist LIMIT %s"""
            params.append(pool)
            cur.execute(sql, tuple(params))
            hits = cur.fetchall()
            persons = _doc_persons(cur, sorted({h[0] for h in hits})) if wanted and not reg_id and (not dict_on or not person_tokens) else {}
    finally:
        if own:
            conn.close()
    best = {}
    for doc_id, chunk_id, text, tokens, dist in hits:  # 문서별 가장 가까운 청크 하나
        if doc_id not in best:
            best[doc_id] = {"doc_id": doc_id, "chunk_id": chunk_id, "masked_text": text,
                            "tokens": tokens, "score": 1 - float(dist)}
    docs = list(best.values())
    if wanted and not reg_id and (not dict_on or not person_tokens):
        # 모순(3) 문서는 k개로 자르기 전에 뺀다. 남은 문서는 기존 순서(거리 → 재정렬) 그대로.
        kept = []
        for d in docs:
            entries = [p for p in persons.get(d["doc_id"], [])
                       if not person_tokens or p["token"] in allowed]
            d["role_grade"] = role_grade(entries, wanted, groups)
            if d["role_grade"] == 3:
                meta["excluded"] += 1
            else:
                kept.append(d)
        docs = rerank_top(masked_q, kept[:k])
    else:
        docs = rerank_top(masked_q, docs[:k])
    return (masked_q, q_tokens, docs, meta) if with_meta else (masked_q, q_tokens, docs)


# ---------------------------------------------------------------- 상위 문서 재정렬 (단어 겹침)
_TOKEN = re.compile(r"<[A-Z_]+_[0-9A-Za-z]+>")
_KEEP_TAGS = ("NNG", "NNP", "VV", "VA", "XR", "SL", "SN")


def _terms(text):
    """토큰을 지운 뒤 Kiwi로 내용어(명사·동사·형용사 등)만 뽑는다."""
    from detection.rule.kiwi_filter import _get_kiwi   # 탐지기가 이미 불러 둔 Kiwi 재사용
    return {t.form for t in _get_kiwi().tokenize(_TOKEN.sub(" ", text))
            if t.tag in ("NNG", "NNP") or (t.tag.startswith(_KEEP_TAGS) and len(t.form) > 1)}


def rerank_top(masked_q, docs):
    """상위 RERANK_TOP(기본 5)개 문서만 '의미 점수 + 가중치 × 질문 단어 겹침'으로 다시 줄 세운다.

    상위 5개에 드는 문서 구성은 바꾸지 않고 순서만 바꾸므로 Hit@5·Recall@5 는 그대로이고,
    '완료/진행 중', '포인트', '감사 지적'처럼 같은 인물 문서끼리 갈리는 핵심 단어로 1등을 고른다.
    holdout 재현 실험(bge-m3, AND): Hit@1 .852 → .898, MRR .904 → .934, 다른 지표 변화 없음.
    RERANK_TOP=0 이면 끈다.
    """
    top = int(config.env("RERANK_TOP", "5"))
    weight = float(config.env("RERANK_WEIGHT", "0.2"))
    if top <= 1 or len(docs) <= 1 or weight <= 0:
        return docs
    q_terms = _terms(masked_q)
    if not q_terms:
        return docs
    head, tail = docs[:top], docs[top:]
    overlap = {d["doc_id"]: len(q_terms & _terms(d["masked_text"])) / len(q_terms) for d in head}
    best = max(overlap.values()) or 1.0
    for d in head:
        d["score"] = d["score"] + weight * overlap[d["doc_id"]] / best
    return sorted(head, key=lambda d: -d["score"]) + tail


def _search_boost(cur, vec, q_tokens, k):
    weight = float(config.env("TOKEN_WEIGHT", "1.0"))
    cur.execute(
        """(SELECT doc_id, chunk_id, masked_text, tokens, embedding <=> %s AS dist
              FROM rag.chunks ORDER BY dist LIMIT %s)
           UNION
           (SELECT doc_id, chunk_id, masked_text, tokens, dist FROM (
              SELECT doc_id, chunk_id, masked_text, tokens, embedding <=> %s AS dist
              FROM rag.chunks WHERE tokens && %s::text[] OFFSET 0) AS tok
            ORDER BY dist LIMIT %s)""",
        (vec, k * 3, vec, q_tokens, TOKEN_POOL))
    hits = cur.fetchall()
    doc_ids = sorted({h[0] for h in hits})
    cur.execute(
        """SELECT doc_id, array_agg(DISTINCT t) FROM rag.chunks, unnest(tokens) AS t
           WHERE doc_id = ANY(%s) AND t = ANY(%s::text[]) GROUP BY doc_id""",
        (doc_ids, q_tokens))
    doc_hits = {doc_id: set(tokens) for doc_id, tokens in cur.fetchall()}  # 문서 단위 토큰 일치
    q_set = set(q_tokens)
    best = {}
    for doc_id, chunk_id, text, tokens, dist in hits:
        overlap = len(q_set & doc_hits.get(doc_id, set())) / len(q_set)
        score = (1 - float(dist)) + weight * overlap
        if doc_id not in best or score > best[doc_id]["score"]:
            best[doc_id] = {"doc_id": doc_id, "chunk_id": chunk_id, "masked_text": text,
                            "tokens": tokens, "score": score}
    return sorted(best.values(), key=lambda d: -d["score"])[:k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--reg-id", help="선택한 확정 인물의 전체 문서 검색 (이름·역할 조건 검색과 구분)")
    a = ap.parse_args()
    masked_q, q_tokens, docs, meta = search(a.query, a.k, with_meta=True, reg_id=a.reg_id)
    print(f"질의(가명처리): {masked_q}\n질의 토큰: {q_tokens or '없음'}")
    print(f"지역 조건: {meta['regions'] or '없음'}  역할 조건: {meta['roles'] or '없음'}  "
          f"역할이 달라 제외: {meta['excluded']}건  인물 후보 수: {meta['person_candidates'] or '없음'}\n")
    if meta.get("ask_person"):
        print("조건에 맞는 문서는 찾았지만 동일인 여부가 불분명한 후보가 여러 명입니다. 답변하려면 추가 단서가 필요합니다.")
    if meta["ask_region"]:
        print(f"어느 지역의 {meta['ask_region']}인가요? 시·도를 함께 알려 주세요.")
    for i, d in enumerate(docs, 1):
        grade = f"  역할등급={d['role_grade']}" if "role_grade" in d else ""
        print(f"{i}. {d['doc_id']}  score={d['score']:.3f}{grade}  tokens={d['tokens'][:4]}")
        print("   " + d["masked_text"][:120].replace("\n", " ") + " …")


if __name__ == "__main__":
    main()
