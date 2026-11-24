"""재질문(CLR) 정의: 답할 수 없는 질문의 원인 판정 보조와 되묻기 문구.

search.py 는 '왜 못 찾았는지'를 meta 에 꼬리표로 남기고, chat.py 는 그 꼬리표로 문구 하나를 고른다.
문구·판정 기준은 이 파일 한 곳에서만 고친다 (재질문 정의서 CLR-01~10 과 같은 번호).

meta 에 남는 값 (search.py)
  too_vague    : CLR-08 질문에 개인정보 토큰·지역·의미 있는 키워드가 하나도 없음
  ask_region   : CLR-01 지역 이름이 여러 시·도에 겹침 (기존)
  ask_doc_type : CLR-10 문서 종류 후보가 여러 개 (동의어 사전이 생기면 채움. 지금은 자리만)
  ask_person   : CLR-02 같은 이름의 후보가 여러 명 (기존)
  empty_reason : 결과 0건의 원인. person(CLR-03) / role(CLR-04) / region(CLR-05) / token(CLR-06)
  empty_label  : empty_reason=token 일 때 질문에 있던 식별자 종류 (address, phone_number …)
  keyword_miss : CLR-07 토큰 없는 질문인데 찾은 문서 어디에도 질문 키워드가 없음

개인정보 원칙: 문구에는 질문의 원래 값(이름·번호·주소)을 다시 넣지 않는다.
넣을 수 있는 것은 식별자 종류, 지역명, 역할어, 후보 수, 문서 종류 이름뿐이다.
"""

# 키워드처럼 뽑히지만 무엇을 찾는지는 알려 주지 않는 말 (요청 동사·지시어·막연한 명사)
# BM25 불용어(starter/bm25.py)와 따로 둔다: 저쪽을 고치면 검색 점수가 바뀌기 때문
VAGUE_WORDS = frozenset({                       # 재질문 판정에만 쓰는 목록
    "알리", "말하", "보이", "주", "찾",          # 알려줘·말해줘·보여줘·주세요·찾아줘 의 동사 부분
    "궁금", "궁금하", "부탁", "요청",             # 궁금해·부탁해
    "사람", "거", "그거", "이거", "저거",          # 그 사람 거 보여줘
    "목록", "리스트", "전체", "전부", "모두",       # 전체 목록 보여줘
    "자료", "기록", "내역", "데이터",              # 기록 알려줘 (무슨 기록인지 없음)
})

# 식별자 토큰 접두어 → 화면에 쓸 이름 (토큰 형식: <PHONE_NUMBER_ab12…>)
LABEL_KO = {                                    # 문구에 원래 값 대신 넣을 종류 이름
    "address": "주소",                            # 주소
    "phone_number": "전화번호",                    # 전화번호
    "email": "이메일",                            # 이메일
    "account_number": "계좌번호",                  # 계좌번호
    "card_number": "카드번호",                     # 카드번호
    "resident_registration_number": "주민등록번호",  # 주민등록번호
    "person": "이름",                             # 이름
}

# 식별자별로 '어떻게 입력하면 찾을 수 있는지' 안내 (CLR-06)
LABEL_HINT = {                                  # 종류별 보충 방법
    "address": "시·도부터 동·호수까지 전체 주소를 입력해야 찾을 수 있어요.",          # 주소는 전체 값으로 토큰을 만든다
    "phone_number": "번호 전체를 입력해 주세요. (하이픈은 있어도 없어도 됩니다)",      # 정규화로 하이픈 차이는 흡수된다
    "email": "@ 뒤 도메인까지 전체 주소를 입력해 주세요.",                         # 이메일 전체
    "account_number": "계좌번호 전체를 입력해 주세요.",                            # 계좌번호 전체
    "card_number": "카드번호 전체를 입력해 주세요.",                               # 카드번호 전체
}

# 원인(꼬리표) → 되묻기 문구. {…} 는 실행할 때 채운다
MESSAGES = {                                    # 재질문 정의서 '② 이렇게 되묻고' 칸
    "too_vague": "어떤 문서를 찾으시나요? 이름이나 전화번호 같은 단서, 문서 종류(VOC, 대출상담 등), 기간 중 하나 이상을 알려 주세요.",  # CLR-08
    "ask_doc_type": "말씀하신 표현에 해당하는 문서 종류가 여러 개예요. {options} 중 어떤 문서를 찾으시나요?",  # CLR-10
    "ask_person": "같은 이름의 후보가 여러 명({n}명) 있어 동일인 여부가 확정되지 않은 상태입니다. 한 사람의 기록으로 합쳐 답하지 않겠습니다. 역할(고객/직원), 지역, 전화번호 같은 다른 단서 중 하나를 더 알려 주세요.",  # CLR-02
    "person": "입력한 이름으로 등록된 인물을 찾지 못했습니다. 이름 표기(띄어쓰기·오타)를 확인하거나, 전화번호·이메일 같은 다른 단서로 물어봐 주세요.",  # CLR-03
    "role": "'{roles}' 역할로는 해당 인물을 찾지 못했습니다. 역할을 바꾸거나 빼고 다시 물어봐 주세요.",  # CLR-04
    "region": "{regions} 지역 문서에서는 찾지 못했습니다. 지역을 다시 확인하거나 지역 조건 없이 물어봐 주세요.",  # CLR-05
    "token": "입력한 {kinds}와(과) 정확히 같은 값이 나오는 문서를 찾지 못했습니다. {hint}",  # CLR-06
    "keyword": "질문과 같은 표현이 들어간 문서를 찾지 못했습니다. 문서에 쓰는 이름으로 다시 물어봐 주세요. (예: VOC요약, 피해접수, 카드분실상담)",  # CLR-07
    "withheld_hint": "찾은 문서 {n}건에는 요청한 정보가 없습니다. 원하는 정보나 문서 종류를 더 구체적으로 알려 주세요.",  # CLR-09 (LLM 답 뒤에 덧붙임)
}
GENERIC_NO_RESULT = "일치하는 문서를 찾지 못했습니다."   # 원인을 모를 때 쓰는 기존 문구

# 재질문으로 처리할 원인 (no_result 중에서 '무엇을 보충할지' 안내하는 것)
NEED_MORE_REASONS = ("person", "role", "region", "token", "keyword")   # CLR-03~07


def meaningful_terms(terms):
    """키워드에서 막연한 말을 뺀 나머지. 이게 비면 무엇을 찾는지 알 수 없다."""
    return {t for t in terms if t not in VAGUE_WORDS}       # VAGUE_WORDS 에 없는 말만 남김


def is_vague(terms, q_tokens, regions):
    """CLR-08: 토큰도 지역도 없고, 막연한 말을 빼면 키워드도 없으면 True."""
    if q_tokens or regions:                     # 이름·번호·지역 중 하나라도 있으면 찾을 단서가 있음
        return False                            # 막연한 질문이 아님
    return not meaningful_terms(terms)          # 남는 키워드가 없으면 막연한 질문


def token_kind(token):
    """<PHONE_NUMBER_ab12> → 'phone_number'."""
    return token[1:].rsplit("_", 1)[0].lower()  # 꺾쇠를 떼고 마지막 _ 앞부분을 소문자로


def identifier_kinds(q_tokens):
    """질문 토큰 중 이름이 아닌 식별자 종류 목록 (중복 없이 정렬)."""
    return sorted({token_kind(t) for t in q_tokens if not t.startswith("<PERSON_")})   # 이름 토큰은 뺌


def _kinds_text(kinds):
    """['address', 'phone_number'] → '주소·전화번호'. 종류가 없으면 '조건'."""
    names = [LABEL_KO.get(k, k) for k in kinds]  # 한국어 이름으로 바꿈 (모르는 종류는 그대로)
    return "·".join(names) or "조건"             # 가운뎃점으로 잇고, 비면 '조건'


def candidate_count(meta):
    """CLR-02 문구에 넣을 후보 수: 이름별 후보 수 중 가장 큰 값."""
    counts = (meta.get("person_candidates") or {}).values()   # {토큰: 후보 수}
    return max(counts, default=2)               # 값이 없으면 '여러 명'의 최소인 2


def pre_answer(meta):
    """검색 결과를 보기 전에 되물어야 하는 경우 (CLR-08 → 01 → 10 → 02 순서).

    반환: (code, 문구) 또는 None. ask_region(CLR-01)은 기존 문구를 chat.py 가 그대로 쓴다.
    """
    if meta.get("too_vague"):                   # CLR-08 조건이 아예 없음
        return "too_vague", MESSAGES["too_vague"]   # 무엇을 찾는지 먼저 묻는다
    if meta.get("ask_region"):                  # CLR-01 지역 겹침 (기존 문구 사용)
        return "ask_region", f"어느 지역의 {meta['ask_region']}인가요? 시·도를 함께 알려 주세요."   # 기존 문구 그대로
    options = meta.get("ask_doc_type") or []    # CLR-10 문서 종류 후보 (동의어 사전이 채움)
    if len(options) > 1:                        # 후보가 2개 이상일 때만 되묻는다
        return "ask_doc_type", MESSAGES["ask_doc_type"].format(options=" / ".join(options))   # 후보를 / 로 나열
    if meta.get("ask_person"):                  # CLR-02 동명 후보
        return "ask_person", MESSAGES["ask_person"].format(n=candidate_count(meta))   # 후보 수만 알려 준다
    return None                                 # 검색 전 재질문 없음


def no_result_message(meta):
    """결과 0건일 때 원인에 맞는 문구 (CLR-03~06). 원인을 모르면 기존 공통 문구."""
    reason = meta.get("empty_reason")           # search.py 가 남긴 원인
    if reason == "person":                      # CLR-03 이름 후보 0명
        return MESSAGES["person"]               # 이름 표기·다른 단서 요청
    if reason == "role":                        # CLR-04 역할 불일치
        roles = "·".join(meta.get("roles") or []) or "요청한"   # 질문의 역할어 (없으면 '요청한')
        return MESSAGES["role"].format(roles=roles)              # 역할 바꾸기·빼기 요청
    if reason == "region":                      # CLR-05 지역 조건 때문에 0건
        regions = "·".join(meta.get("regions") or []) or "요청한"  # 질문의 지역명
        return MESSAGES["region"].format(regions=regions)          # 지역 재확인 요청
    if reason == "token":                       # CLR-06 식별자 불일치
        kinds = meta.get("empty_label") or []   # 질문에 있던 식별자 종류
        hint = LABEL_HINT.get(kinds[0], "값 전체를 정확히 입력해 주세요.") if len(kinds) == 1 \
            else "값 전체를 정확히 입력했는지 확인하거나, 조건을 하나씩 나눠 물어봐 주세요."   # 종류가 여럿이면 나눠 묻기
        return MESSAGES["token"].format(kinds=_kinds_text(kinds), hint=hint)   # 종류 이름 + 입력 방법
    return GENERIC_NO_RESULT                    # 원인 불명: 기존 문구 유지


def withheld_hint(n_docs):
    """CLR-09: LLM 이 '확인할 수 없다'고 답했을 때 덧붙이는 문장."""
    return MESSAGES["withheld_hint"].format(n=n_docs)   # 찾은 문서 수만 넣는다
