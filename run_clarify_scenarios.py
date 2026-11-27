"""재질문 정의서 '테스트 시나리오' 시트를 채우기 위한 실행 스크립트.

같은 질문을 두 브랜치에서 돌려 결과를 비교한다.
    git switch develop      && python run_clarify_scenarios.py --tag before   # 적용 전
    git switch HTW-clarify  && python run_clarify_scenarios.py --tag after    # 적용 후
    python run_clarify_scenarios.py --compare                                  # 두 결과를 표로 비교

- 레포 폴더(nlp_like_lion_chatbot_03)에서 실행한다. 이 파일은 git 이 관리하지 않으므로 브랜치를 바꿔도 남아 있다.
- '(holdout 실제 이름)' 같은 자리는 DB 에서 자동으로 골라 채운다 (동명이인이 가장 많은 이름, 고객으로만 있는 이름, 실제 주소).
- 화면 출력에는 실제 이름·주소 대신 [동명이인], [고객전용], [주소] 같은 표시만 나온다. 그대로 복사해서 보내면 된다.
- DB 는 읽기만 한다. LLM 키가 있으면 실제로 답변을 만든다 (질문 24개 → 몇 분 걸릴 수 있음).
- 질문 사이에 3초씩 쉰다 (LLM 호출 한도 429 예방). --sleep 5 처럼 바꿀 수 있다.
- 노출 점검: python run_clarify_scenarios.py --probe
- 이어받기(번호만·역할만 보충): python run_clarify_scenarios.py --merge
"""
import argparse  # 명령줄 옵션
import json  # 결과 저장
import os  # 현재 폴더를 import 경로에 넣기
import re  # 후보 수(○명) 찾기
import sys  # import 경로
import time  # 걸린 시간 측정
from pathlib import Path  # 파일 경로

sys.path.insert(0, os.getcwd())  # 레포 폴더를 import 경로 맨 앞에 넣는다

OUT = Path("outputs/clarify_scenarios")  # 결과 저장 폴더


def pick_values():
    """질문의 빈자리에 넣을 실제 값을 DB 에서 고른다.

    반환: AMBIG(동명이인이 가장 많은 이름), CUST(고객으로만 나오는 이름), ADDR(호수까지 있는 주소),
          CUST_TYPE(CUST 가 가장 많이 나오는 문서 종류), ADDR_TYPE(그 주소가 나오는 문서 종류),
          PHONE(인물사전이 AMBIG 후보 중 한 명에게만 귀속한 그 사람 본인의 전화번호 → 동명이인을 좁히는 보충 단서)
    """
    from starter import db  # DB 연결 도우미 (지연 import: --compare 때는 필요 없음)
    with db.rag_conn() as rc, rc.cursor() as cur:  # 검색용 계정 (look_up·chunks 읽기)
        cur.execute("""
            SELECT token,
                   count(DISTINCT coalesce(reg_id, person_id)) FILTER (WHERE role = 'customer') AS cust_ids,  -- 고객 후보 수
                   count(DISTINCT coalesce(reg_id, person_id)) AS all_ids,                                   -- 전체 후보 수
                   bool_and(role = 'customer') AS only_customer,                                              -- 고객으로만 나오는지
                   count(*) AS n                                                                              -- 등장 횟수
            FROM rag.look_up GROUP BY token""")  # 이름 토큰별 통계
        stats = cur.fetchall()  # (token, cust_ids, all_ids, only_customer, n)
        ambig_list = [r[0] for r in sorted(stats, key=lambda r: (r[1], r[4]), reverse=True) if r[1] >= 2]   # 고객 후보가 2명 이상인 이름 (많은 순)
        ambig = ambig_list[0] if ambig_list else max(stats, key=lambda r: (r[1], r[4]))[0]  # 기본: 고객 후보가 가장 많은 이름
        singles = [r for r in stats if r[3] and r[2] == 1]                                  # 고객으로만, 후보 1명인 이름
        cust = max(singles, key=lambda r: r[4])[0] if singles else None                     # 그중 가장 자주 나오는 이름
        cust_type = None
        if cust:                                                                            # 그 사람이 가장 많이 나오는 문서 종류
            cur.execute("""SELECT c.doc_type FROM rag.look_up l JOIN rag.chunks c ON c.chunk_id = l.chunk_id
                           WHERE l.token = %s AND c.doc_type IS NOT NULL
                           GROUP BY 1 ORDER BY count(*) DESC LIMIT 1""", (cust,))
            row = cur.fetchone()
            cust_type = row[0] if row else None
    with db.vault_conn() as vc, vc.cursor() as cur:  # 원래 값·인물사전 귀속 결과는 vault 에만 있다
        phone_tok = None  # 동명이인을 좁힐 본인 번호
        for tok in ambig_list:  # 동명이인 이름을 후보 많은 순으로 보며, 본인 번호가 있는 이름을 고른다
            cur.execute("""SELECT t FROM vault.person_info p, unnest(p.info_label) AS t
                           WHERE p.token = %s AND p.role = 'customer' AND t LIKE '<PHONE_NUMBER_%%'
                           GROUP BY t HAVING count(DISTINCT coalesce(p.reg_id, p.person_id)) = 1
                           LIMIT 1""", (tok,))  # 인물사전이 동명이인 후보 중 '한 명에게만' 귀속한 그 사람 본인의 번호
            row = cur.fetchone()
            if row:  # 찾으면 이 이름을 동명이인 시나리오에 쓴다
                ambig, phone_tok = tok, row[0]
                break

        def original(tok):  # 토큰 → 원래 값
            if not tok:
                return None
            cur.execute("SELECT originals[1] FROM vault.token_map WHERE token = %s", (tok,))
            r = cur.fetchone()
            return r[0] if r else None
        cur.execute("""SELECT token, originals[1] FROM vault.token_map
                       WHERE token LIKE '<ADDRESS_%%' AND originals[1] ~ '[0-9]+호'
                       ORDER BY length(originals[1]) DESC LIMIT 1""")  # 호수까지 있는 주소 하나
        row = cur.fetchone()
        addr_tok, addr = (row[0], row[1]) if row else (None, None)
        names = {"AMBIG": original(ambig), "CUST": original(cust), "PHONE": original(phone_tok)}
    addr_type = None
    if addr_tok:                                                                            # 그 주소가 나오는 문서 종류
        with db.rag_conn() as rc, rc.cursor() as cur:
            cur.execute("SELECT doc_type FROM rag.chunks WHERE tokens @> ARRAY[%s] AND doc_type IS NOT NULL LIMIT 1", (addr_tok,))
            row = cur.fetchone()
            addr_type = row[0] if row else None
    from starter import address  # 여러 시·도에 같은 이름이 있는 시군구 목록
    amb_names = sorted(k for k, v in address._SIGUNGU_OWNERS.items() if len(v) > 1)  # 중구·동구·서구 …
    region_word, region_full = "중구", "서울 중구"  # 기본값
    with db.rag_conn() as rc, rc.cursor() as cur:  # 실제로 문서가 있는 '○○ 중구/동구 …' 를 고른다
        cur.execute("""SELECT r FROM rag.chunks, unnest(regions) AS r
                       WHERE split_part(r, ' ', 2) = ANY(%s) GROUP BY r ORDER BY count(*) DESC LIMIT 1""", (amb_names,))
        row = cur.fetchone()
        if row:
            region_full = row[0]                      # 예: '대구광역시 동구'
            region_word = region_full.split(" ")[1]   # 예: '동구' (1차 질문에 쓰는 애매한 이름)
    return {**names, "ADDR": addr, "CUST_TYPE": cust_type or "상담 내역", "ADDR_TYPE": addr_type or "문서",
            "REGION_WORD": region_word, "REGION_FULL": region_full}


def partial_address(addr):
    """'… 174, 502호' → '… 174' (호수를 뺀 주소)."""
    return re.sub(r",?\s*[0-9]+\s*호.*$", "", addr).strip() if addr else None  # 호수 이후를 지운다


def scenarios(v):
    """테스트 시나리오 시트와 같은 순서의 (코드, 1차 질문, 보충 질문).
    보충 질문은 그 사람이 실제로 나오는 문서 종류(CUST_TYPE)와, 동명이인을 한 명으로 좁히는 전화번호(PHONE)로 만든다."""
    a, c, full = v["AMBIG"], v["CUST"], v["ADDR"]  # 동명이인 이름, 고객전용 이름, 전체 주소
    part = partial_address(full)  # 호수를 뺀 주소
    ct, at = v["CUST_TYPE"], v["ADDR_TYPE"]  # 고객전용 이름이 나오는 문서 종류, 주소가 나오는 문서 종류
    clue = f"{a} 고객 전화번호 {v['PHONE']} 상담 내역 알려줘" if v.get("PHONE") else f"{a} 고객 대출 상담 내역 알려줘"   # 좁히는 단서
    return [
        ("CLR-08", "알려줘", "보이스피싱 피해 접수 절차 알려줘"),
        ("CLR-08", "그 사람 거 보여줘", "법인카드 사용 보고서 찾아줘"),
        ("CLR-01", f"{v['REGION_WORD']} 사는 고객 알려줘", f"{v['REGION_FULL']} 사는 고객 알려줘"),
        ("CLR-10", "민원 내역 보여줘", "민원처리 내역 보여줘"),
        ("CLR-02", f"{a} 고객 상담 내역 알려줘", clue),
        ("CLR-03", f"가나다 고객 {ct} 보여줘", f"{c} 고객 {ct} 보여줘"),
        ("CLR-04", f"직원 {c} {ct} 보여줘", f"{c} 고객 {ct} 보여줘"),
        ("CLR-05", f"제주 사는 {c} 고객 {ct} 보여줘", f"{c} 고객 {ct} 보여줘"),
        ("CLR-06", f"{part} 주소가 나온 {at} 보여줘", f"{full} 주소가 나온 {at} 보여줘"),
        ("CLR-07", "클레임 정리본 보여줘", "고객 VOC 해결 완료 건"),
        ("자동 치환", "고객의 소리 요약 보여줘", None),
        ("CLR-09", f"{c} 고객 대출 금리 알려줘", f"{c} 고객 {ct} 내용 알려줘"),
    ]


def hide(text, v):
    """화면 출력용: 실제 이름·주소를 표시어로 바꾼다 (결과를 밖으로 보내도 안전하게)."""
    for key, label in (("ADDR", "[주소]"), ("PHONE", "[전화번호]"), ("AMBIG", "[동명이인]"), ("CUST", "[고객전용]")):  # 긴 값(주소)부터
        if v.get(key):
            text = text.replace(v[key], label)  # 전체 주소
    part = partial_address(v.get("ADDR"))
    return text.replace(part, "[주소-호수뺌]") if part else text  # 호수를 뺀 주소


SLEEP = 3.0  # 질문 사이 대기 초 (LLM 호출 한도 429 예방, --sleep 으로 변경)


def run_one(question, v):
    """질문 하나를 챗봇과 같은 함수로 돌리고, 시트에 필요한 값만 뽑는다."""
    from starter import chat  # 현재 브랜치의 chat.py
    time.sleep(SLEEP)  # 앞 질문과 간격을 둔다 (429 예방)
    t0 = time.time()  # 시작 시각
    try:
        res = chat.answer(question, 3)  # 화면과 같은 k=3
    except Exception as exc:  # 실패해도 다음 질문은 계속
        return {"q": hide(question, v), "state": "error", "error": type(exc).__name__}
    answer = res.get("answer") or ""  # 답변 (가명 토큰 상태, 원래 값 복원 전)
    docs = [d["doc_id"] for d in res.get("docs", [])]  # 화면에 나갈 문서 번호
    asked = res.get("state") in ("clarify", "no_result") or res.get("clarify_code")  # 재질문·못 찾음 응답인지
    return {
        "q": hide(question, v),  # 질문 (실제 값 숨김)
        "state": res.get("state"),  # 응답 종류
        "clarify_code": res.get("clarify_code"),  # 재질문 원인 (적용 전 브랜치에는 없음 → None)
        "docs": docs,  # 문서 번호
        "count_shown": bool(re.search(r"[0-9]+\s*명", answer)) if asked else False,  # 재질문 문구에 후보 수(○명)가 있는지
        "docs_shown": bool(docs) if asked else False,  # 재질문·못 찾음인데 문서 번호를 보여 주는지
        "answer": hide(answer, v)[:160],  # 답변 앞부분
        "sec": round(time.time() - t0, 1),  # 걸린 시간
    }


def run(tag):
    """시나리오 전체를 돌려 outputs/clarify_scenarios/<tag>.json 에 저장하고 요약을 출력한다."""
    v = pick_values()  # 빈자리에 넣을 실제 값
    print(f"고른 값: 동명이인={'있음' if v['AMBIG'] else '없음'} 고객전용={'있음' if v['CUST'] else '없음'} 주소={'있음' if v['ADDR'] else '없음'}"
          f" 좁히는전화번호={'있음' if v.get('PHONE') else '없음'} 문서종류={v['CUST_TYPE']}/{v['ADDR_TYPE']}"
          f" 애매한지역={v['REGION_WORD']}→{v['REGION_FULL']}\n")
    rows = []
    for code, first, follow in scenarios(v):  # 시트 순서대로
        r1 = run_one(first, v)  # 1차 질문
        r2 = run_one(follow, v) if follow else None  # 보충 질문
        rows.append({"code": code, "first": r1, "follow": r2})
        print(f"[{code}] 1차 {r1['state']}/{r1.get('clarify_code')} 후보수노출={r1.get('count_shown')} 문서노출={r1.get('docs_shown')}"
              + (f" → 보충 {r2['state']} 문서={r2.get('docs')}" if r2 else ""))  # 진행 상황 한 줄
    OUT.mkdir(parents=True, exist_ok=True)  # 저장 폴더
    path = OUT / f"{tag}.json"
    path.write_text(json.dumps({"tag": tag, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")  # 결과 저장
    print(f"\n저장: {path}")


def compare():
    """before.json 과 after.json 을 시트 칸 순서대로 나란히 출력한다 (이 출력을 그대로 보내면 된다)."""
    before = json.loads((OUT / "before.json").read_text(encoding="utf-8"))["rows"]  # 적용 전
    after = json.loads((OUT / "after.json").read_text(encoding="utf-8"))["rows"]  # 적용 후
    print("코드\t1차 질문\t적용 전 state\t적용 후 state\t적용 후 원인\t보충 후 state\t보충 후 문서\t후보 수 노출\t문서 번호 노출\t적용 후 1차 답변")
    for b, a in zip(before, after):  # 같은 순서의 두 결과
        f = a["follow"] or {}  # 적용 후 보충 질문 결과
        print("\t".join(str(x) for x in (
            a["code"], a["first"]["q"], b["first"]["state"], a["first"]["state"], a["first"].get("clarify_code"),
            f.get("state", "-"), ",".join(f.get("docs", [])) or "-",
            "O" if a["first"].get("count_shown") else "X", "O" if a["first"].get("docs_shown") else "X",
            a["first"].get("answer", "").replace("\n", " "))))  # 한 줄에 탭으로 구분


def unified_text():
    """현재 브랜치의 통일 문장 (starter/clarify.py 에 있으면 그 값, 없으면 정의서 문장)."""
    try:
        from starter.clarify import UNIFIED  # 통일 응답이 들어간 브랜치
        return UNIFIED
    except ImportError:  # develop 등 통일 응답이 없는 브랜치
        return ("이 질문만으로는 답변할 수 있는 정보가 없습니다. 역할(고객/직원), 지역, 전화번호 같은 조건이나 "
                "찾으시는 업무·문서 종류를 함께 넣어 다시 질문해 주세요.")


def merge_scenarios(v):
    """이어받기 시나리오: (코드, 1차 질문, 조각 보충 답, 기대). 보충은 문장 전체가 아니라 조각만 보낸다."""
    a, c, full = v["AMBIG"], v["CUST"], v["ADDR"]  # 동명이인 이름, 고객전용 이름, 전체 주소
    ct, at = v["CUST_TYPE"], v["ADDR_TYPE"]  # 문서 종류
    return [
        ("CLR-02", f"{a} 고객 상담 내역 알려줘", v.get("PHONE") or "010-0000-0000", "번호로 1명 좁혀 답변"),
        ("CLR-03", f"가나다 고객 {ct} 보여줘", c, "이름 교체 후 답변"),
        ("CLR-04", f"직원 {c} {ct} 보여줘", "고객이요", "역할 교체 후 답변"),
        ("CLR-06", f"{partial_address(full)} 주소가 나온 {at} 보여줘", full, "주소 교체 후 답변"),
        ("CLR-01", f"{v['REGION_WORD']} 사는 고객 알려줘", v["REGION_FULL"], "지역 확정 후 검색"),
        ("새 질문", f"{a} 고객 상담 내역 알려줘", "법인카드 사용 보고서 찾아줘", "이어받지 않고 새 질문으로"),
    ]


def merge_run():
    """조각 보충 답을 직전 재질문과 이어 붙여 답이 나오는지 본다 (현재 브랜치에 이어받기가 있어야 함)."""
    from starter import chat  # 현재 브랜치의 chat.py
    v = pick_values()  # 실제 값
    print("코드\t1차 질문\t1차 원인\t보충 조각\t이어받음\t보충 후 state\t보충 후 원인\t보충 후 문서\t기대\t보충 후 답변")
    for code, first, frag, expect in merge_scenarios(v):
        time.sleep(SLEEP)  # 429 예방
        r1 = chat.answer(first, 3)  # 1차 질문
        ctx = {"masked_query": r1.get("masked_query"), "query_tokens": r1.get("query_tokens"),
               "clarify_code": r1.get("clarify_code")}  # app.py 가 감사 로그에서 꺼내는 값과 같은 것
        time.sleep(SLEEP)
        try:
            r2 = chat.answer(frag, 3, context=ctx)  # 조각 보충 답 + 직전 재질문
        except TypeError:  # 이어받기가 없는 브랜치
            r2 = chat.answer(frag, 3)
        print("\t".join(str(x) for x in (
            code, hide(first, v), r1.get("clarify_code"), hide(frag, v), "O" if r2.get("merged") else "X",
            r2.get("state"), r2.get("clarify_code"), ",".join(d["doc_id"] for d in r2.get("docs", [])) or "-",
            expect, hide((r2.get("answer") or "").replace("\n", " "), v)[:120])))


def badge(res):
    """화면 배지를 app.py·index.html 과 같은 규칙으로 계산한다."""
    if res.get("clarify_code"):  # 재질문 원인이 있으면 (need_more)
        return "조건 확인 필요"
    return "아하!" if res.get("state") in ("answered", "search_only") else "확인 불가"   # 나머지는 기존 규칙


def probe():
    """'노출 점검' 시트 5줄: 같은 사람을 조건만 바꿔 물었을 때 화면 응답이 같은지 본다 (현재 브랜치 기준)."""
    from starter import chat  # 현재 브랜치의 chat.py
    v = pick_values()  # 실제 이름 고르기
    c = v["CUST"]  # 고객으로만 있는 이름
    rows = [
        (1, "ask_person", f"{v['AMBIG']} 고객 상담 내역 알려줘"),  # 동명이인
        (2, "person", "가나다 고객 상담 내역 알려줘"),             # 없는 이름
        (3, "role", f"직원 {c} 기록 보여줘"),                     # 역할 불일치
        (4, "region", f"제주 사는 {c} 고객 상담 내역"),            # 지역 불일치
        (5, "token", "010-0000-0000 고객 상담 내역"),             # 문서에 없을 전화번호
    ]
    results = []
    unified = unified_text()  # 비교할 통일 문장
    for no, expect, q in rows:  # 다섯 줄
        time.sleep(SLEEP)  # 429 예방
        res = chat.answer(q, 3)  # 화면과 같은 함수
        ans = (res.get("answer") or "").strip()  # 화면 문장
        results.append((no, expect, q, res, ans))
    badges = {badge(r[3]) for r in results}  # 다섯 줄의 배지 종류
    print("번호\t질문\t실제 원인\t기대 원인\t화면 응답이 통일 문장과 같음\t후보 수 노출\t문서 번호·조각 노출\t배지가 같음\t배지\t화면 응답")
    for no, expect, q, res, ans in results:
        print("\t".join(str(x) for x in (
            no, hide(q, v), res.get("clarify_code"), expect,
            "O" if ans.startswith(unified) else "X",                       # 통일 문장으로 시작하는지 (주소 안내가 뒤에 붙을 수 있음)
            "O" if re.search(r"[0-9]+\s*명", ans) else "X",                 # ○명 이 보이는지
            "O" if res.get("docs") else "X",                               # 문서 번호·조각이 화면으로 나가는지
            "O" if len(badges) == 1 else "X",                              # 다섯 줄 배지가 모두 같은지
            badge(res), hide(ans, v).replace("\n", " ")[:120])))           # 실제 배지와 응답 앞부분
    same = len({r[4] for r in results}) == 1  # 다섯 응답 문장이 완전히 같은지
    print(f"\n다섯 응답 문장이 모두 같음: {'O' if same else 'X'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()  # 옵션 정의
    ap.add_argument("--tag", choices=["before", "after"])  # 적용 전/후 중 무엇을 돌릴지
    ap.add_argument("--compare", action="store_true")  # 두 결과 비교 출력
    ap.add_argument("--probe", action="store_true")  # 노출 점검 5줄 실행
    ap.add_argument("--merge", action="store_true")  # 이어받기(조각 보충 답) 시나리오
    ap.add_argument("--sleep", type=float, default=3.0)  # 질문 사이 대기 초 (429 가 계속 나면 늘린다)
    a = ap.parse_args()
    SLEEP = a.sleep  # 대기 시간 반영
    if a.merge:  # 이어받기
        merge_run()
    elif a.probe:  # 노출 점검
        probe()
    elif a.compare:  # 비교만
        compare()
    elif a.tag:  # 실행
        run(a.tag)
    else:
        ap.print_help()  # 옵션이 없으면 사용법
