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

응답 그룹 (재질문 정의서 '응답 원칙' 시트)
  A 구체 안내 : 사람과 무관한 원인 (CLR-08·01·10·07·09, 토큰 없는 질문의 CLR-04·05)
                → 원인별로 무엇을 보충할지 구체적으로 안내
  B 통일 응답 : 사람·식별자가 걸린 원인 (CLR-02·03·06, 토큰 있는 질문의 CLR-04·05, 토큰 있는 원인 불명 0건)
                → 원인과 관계없이 같은 문장(UNIFIED) 하나. 후보 수·문서 번호·문서 조각은 화면에 보내지 않는다
                  원인마다 문구가 다르면 조건을 바꿔 가며 물어 그 사람의 존재·역할·지역을 추측할 수 있기 때문
  입력 방법 안내(ADDRESS_HINT)는 '못 찾은 원인'이 아니라 '질문에 주소가 들어 있는지'로만 붙인다.

개인정보 원칙: 문구에는 질문의 원래 값(이름·번호·주소)을 다시 넣지 않는다.
원인(clarify_code)은 감사 로그에만 남고 사용자 화면에는 드러나지 않는다.
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

# 원인(꼬리표) → 되묻기 문구. {…} 는 실행할 때 채운다
MESSAGES = {                                    # 재질문 정의서 '② 이렇게 되묻고' 칸
    "too_vague": "어떤 문서를 찾으시나요? 이름이나 전화번호 같은 단서, 문서 종류(VOC, 대출상담 등), 기간 중 하나 이상을 알려 주세요.",  # CLR-08
    "ask_doc_type": "말씀하신 표현에 해당하는 문서 종류가 여러 개예요. {options} 중 어떤 문서를 찾으시나요?",  # CLR-10
    "role": "요청한 역할로는 해당 문서를 찾지 못했습니다. 역할을 바꾸거나 빼고 다시 물어봐 주세요.",  # CLR-04 (토큰 없는 질문만)
    "region": "{regions} 지역 문서에서는 찾지 못했습니다. 지역을 다시 확인하거나 지역 조건 없이 물어봐 주세요.",  # CLR-05 (토큰 없는 질문만)
    "keyword": "질문과 같은 표현이 들어간 문서를 찾지 못했습니다. 문서에 쓰는 이름으로 다시 물어봐 주세요. (예: VOC요약, 피해접수, 카드분실상담)",  # CLR-07
    "withheld_hint": "찾은 문서 {n}건에는 요청한 정보가 없습니다. 원하는 정보나 문서 종류를 더 구체적으로 알려 주세요.",  # CLR-09 (LLM 답 뒤에 덧붙임)
}
GENERIC_NO_RESULT = "일치하는 문서를 찾지 못했습니다."   # 토큰 없는 질문에서 원인을 모를 때 쓰는 기존 문구

# B 그룹 통일 응답: 동명이인·이름 없음·역할 불일치·지역 불일치·식별자 불일치를 화면에서 구분할 수 없게 한다
UNIFIED = ("이 질문만으로는 답변할 수 있는 정보가 없습니다. 역할(고객/직원), 지역, 전화번호 같은 조건이나 "
           "찾으시는 업무·문서 종류를 함께 넣어 다시 질문해 주세요.")   # '정보가 없다'가 아니라 '이 질문만으로는' (동명이인에도 맞는 말)
                                                                       # 역할·지역·번호를 앞에 둔 이유: 동명이인 후보를 실제로 좁히는 조건 (search._narrow_candidates)
ADDRESS_HINT = "주소는 시·도부터 동·호수까지 전체를 입력해야 찾을 수 있어요."   # 질문에 주소가 있으면 원인과 관계없이 덧붙임
PERSON_GROUP = frozenset({"ask_person", "person", "token"})    # 언제나 B 그룹인 원인 (role·region 은 토큰 유무로 결정)



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


def is_person_group(code, q_tokens):
    """이 원인을 B 그룹(통일 응답)으로 처리할지. 질문에 이름·식별자 토큰이 있으면 역할·지역·원인 불명도 B."""
    if code in PERSON_GROUP:                    # 동명이인·이름 없음·식별자 불일치
        return True                             # 언제나 통일 응답
    return bool(q_tokens) and code in ("role", "region", None)   # 토큰이 걸린 역할·지역·원인 불명 0건도 통일 응답


def unified_message(q_tokens):
    """B 그룹 화면 문장. 질문에 주소가 있으면(원인과 무관하게) 전체 주소 입력 안내를 덧붙인다."""
    has_address = any(token_kind(t) == "address" for t in q_tokens)   # 사용자가 입력한 내용만 본다
    return f"{UNIFIED}\n{ADDRESS_HINT}" if has_address else UNIFIED   # 주소가 있으면 한 줄 추가


def pre_answer(meta, q_tokens=()):
    """검색 결과를 보기 전에 되물어야 하는 경우 (CLR-08 → 01 → 10 → 02 순서).

    반환: (code, 문구) 또는 None.
    """
    if meta.get("too_vague"):                   # CLR-08 조건이 아예 없음
        return "too_vague", MESSAGES["too_vague"]   # 무엇을 찾는지 먼저 묻는다
    if meta.get("ask_region"):                  # CLR-01 지역 겹침 (기존 문구 사용)
        return "ask_region", f"어느 지역의 {meta['ask_region']}인가요? 시·도를 함께 알려 주세요."   # 기존 문구 그대로
    options = meta.get("ask_doc_type") or []    # CLR-10 문서 종류 후보 (동의어 사전이 채움)
    if len(options) > 1:                        # 후보가 2개 이상일 때만 되묻는다
        return "ask_doc_type", MESSAGES["ask_doc_type"].format(options=" / ".join(options))   # 후보를 / 로 나열
    if meta.get("ask_person"):                  # CLR-02 동명 후보 → B 그룹 통일 응답 (후보 수 안 알림)
        return "ask_person", unified_message(q_tokens)
    return None                                 # 검색 전 재질문 없음


def no_result_message(meta, q_tokens=()):
    """결과 0건일 때 화면 문장 (CLR-03~06). 사람·식별자가 걸리면 원인과 관계없이 통일 응답."""
    reason = meta.get("empty_reason")           # search.py 가 남긴 원인 (감사 로그용)
    if is_person_group(reason, q_tokens):       # B 그룹
        return unified_message(q_tokens)        # 같은 문장 (+ 주소 안내)
    if reason == "role":                        # 토큰 없는 역할 질문 (예: 직원 VOC) → 사람과 무관
        return MESSAGES["role"]                 # 역할 바꾸기·빼기 요청 (내부 역할 코드는 쓰지 않음)
    if reason == "region":                      # 토큰 없는 지역 질문 (예: 제주 VOC)
        regions = "·".join(meta.get("regions") or []) or "요청한"  # 질문의 지역명 (사용자가 입력한 것)
        return MESSAGES["region"].format(regions=regions)          # 지역 재확인 요청
    return GENERIC_NO_RESULT                    # 토큰 없는 질문의 원인 불명: 기존 문구 유지


def withheld_hint(n_docs):
    """CLR-09: LLM 이 '확인할 수 없다'고 답했을 때 덧붙이는 문장."""
    return MESSAGES["withheld_hint"].format(n=n_docs)   # 찾은 문서 수만 넣는다
