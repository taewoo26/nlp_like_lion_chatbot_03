"""직접 작성한 합성 문맥 예제. [[이름]] 표시를 정답 위치로 변환한다.

실행: python -m eval.build_context_cases
dev는 개선용, check는 개선 조건 고정 후 한 번 확인하는 용도다.
기존 dev/test 및 프로젝트 holdout과 독립된 파일이며 독립 표본 성능 추정은 아니다.
"""
import hashlib
import json
import re
from pathlib import Path


# (유형, 개인정보가 아닌 문맥, 실제 이름인 문맥). 각 쌍은 같은 split에 둔다.
DEV = [
    ('product', '하린 포인트 카드가 출시됐다.', '[[하린]] 씨에게 출시 일정을 전달했다.'),
    ('product', '신상품 안내: 봄날 적금의 가입 기간입니다.', '담당자 [[봄날]] 님이 가입 기간을 안내했다.'),
    ('product', '다온 체크카드는 연회비가 없습니다.', '[[다온]]이가 체크카드를 신청했다.'),
    ('product', '상품명: 새봄 보험\n보장 내용을 확인하세요.', '고객명: [[새봄]]\n보험 보장 내용을 확인하세요.'),
    ('company', '미르보안과 점검 계약을 체결했다.', '[[김보안]] 담당자와 점검 일정을 협의했다.'),
    ('company', '참석 업체: 다온정보\n회의록을 첨부합니다.', '참석자: [[김다온]]\n회의록을 첨부합니다.'),
    ('company', '한울기술의 대표이사가 회신했다.', '[[한울]] 씨가 회신했다.'),
    ('company', '계약 상대방은 새봄솔루션입니다.', '계약 담당자는 [[박새봄]]입니다.'),
    ('verb', '부장님이 내일까지 보고서를 달래.', '[[달래]]가 내일까지 보고서를 보내기로 했다.'),
    ('verb', '접수 경로: 금융감독원 이첩', '방문자: [[이첩]] 씨'),
    ('verb', '교육 미이수 시 다시 신청해야 한다.', '[[이수]] 씨가 교육을 신청했다.'),
    ('verb', '자료를 보내 달라고 요청했다.', '[[보내]] 씨에게 자료를 요청했다.'),
    ('field', '갑: 새봄카드\n을: 다솜콜솔루션', '[[김민준]]: 계약서를 확인했습니다.'),
    ('field', '대상: 전체 임직원\n교육을 안내합니다.', '[[은서]]: 교육 일정을 확인했어.'),
    ('field', '고객명: [[이재원]]\n연락을 요청했다.', '[[하린]]: [[이재원]]에게 연락했어.'),
    ('field', '담당자: [[김은영]]\n소속: 운영팀', '[[김은영]]: 운영팀에서 담당합니다.'),
    ('boundary', '고객: [[전 서윤]] 님이 방문했다.', '고객: [[전서윤]]님이 방문했다.'),
    ('boundary', '수신: [[남 지우]] 씨', '수신: [[남지우]]씨'),
    ('boundary', '[[이 하늘]] 님께 전달했다.', '[[이하늘]]님께 전달했다.'),
    ('boundary', '담당 [[허 수아]] 님에게 문의했다.', '담당 [[허수아]]님에게 문의했다.'),
    ('title', '허 부장님께 보고했다.', '[[허민수]] 부장님께 보고했다.'),
    ('title', '남 부장이 회의에 참석했다.', '[[남지훈]] 부장이 회의에 참석했다.'),
    ('title', '이 차장에게 전달했다.', '[[이서연]] 차장에게 전달했다.'),
    ('title', '강 팀장님이 확인했다.', '[[강수진]] 팀장님이 확인했다.'),
]

CHECK = [
    ('product', '초롱 여행카드의 혜택을 비교했다.', '상담원 [[초롱]] 님이 혜택을 비교했다.'),
    ('product', '나래 저축상품은 다음 달 판매한다.', '[[나래]] 씨는 다음 달 방문한다.'),
    ('product', '상품 안내서에 온유 연금을 소개했다.', '안내서를 [[온유]]에게 보냈다.'),
    ('product', '신규 서비스 이름은 여울입니다.', '신규 담당자 이름은 [[여울]]입니다.'),
    ('company', '외주 업체 누리보안에 재점검을 맡겼다.', '외주 담당 [[윤보안]]에게 재점검을 맡겼다.'),
    ('company', '해솔데이터에서 견적서를 받았다.', '[[최해솔]]에게 견적서를 받았다.'),
    ('company', '바른기술 소속 직원이 방문했다.', '[[바른]] 님이 방문했다.'),
    ('company', '연두솔루션은 공급업체입니다.', '[[정연두]]는 공급 담당자입니다.'),
    ('verb', '실장님이 이번 주까지 신청서를 달래.', '신청자는 [[달래]]이며 이번 주 방문한다.'),
    ('verb', '접수 경로: 담당 기관 이첩', '신청인 [[이첩]] 님의 서류를 받았다.'),
    ('verb', '교육 미이수자는 보충 교육을 듣는다.', '보충 교육 신청자: [[이수]]'),
    ('verb', '서류를 보내 달라는 답장이 왔다.', '답장을 작성한 사람은 [[보내]] 씨다.'),
    ('field', '갑: 누리은행\n을: 해솔데이터', '[[정연두]]: 수정한 계약서입니다.'),
    ('field', '대상: 신규 입사자\n서류 제출 안내', '[[나래]]: 서류는 내일 낼게.'),
    ('field', '고객명: [[윤지호]]\n방문 일정을 잡았다.', '[[초롱]]: [[윤지호]]에게 일정을 물었어.'),
    ('field', '책임자: [[백도현]]\n부서: 감사팀', '[[백도현]]: 감사팀 회의에 참석합니다.'),
    ('boundary', '민원인 [[문 가은]] 님의 요청이다.', '민원인 [[문가은]]님의 요청이다.'),
    ('boundary', '[[유 다현]] 씨에게 회신했다.', '[[유다현]]씨에게 회신했다.'),
    ('boundary', '발신자: [[오 지안]] 님', '발신자: [[오지안]]님'),
    ('boundary', '신청인 [[안 소희]] 님께 안내했다.', '신청인 [[안소희]]님께 안내했다.'),
    ('title', '최 과장님은 출장 중이다.', '[[최유진]] 과장님은 출장 중이다.'),
    ('title', '박 실장에게 결재를 요청했다.', '[[박예담]] 실장에게 결재를 요청했다.'),
    ('title', '정 대리가 검토하기로 했다.', '[[정시온]] 대리가 검토하기로 했다.'),
    ('title', '윤 사원님과 면담했다.', '[[윤채원]] 사원님과 면담했다.'),
]


def make_doc(identifier, split, category, pair_id, marked):
    text, entities, cursor = '', [], 0
    for match in re.finditer(r'\[\[(.+?)\]\]', marked):
        text += marked[cursor:match.start()]
        value = match.group(1)
        start = len(text)
        text += value
        entities.append(dict(label='person', start=start, end=len(text), text=value))
        cursor = match.end()
    text += marked[cursor:]
    assert '[[' not in text and ']]' not in text
    return dict(id=identifier, split=split, category=category, pair_id=pair_id,
                text=text, entities=entities, hard_cases=[])


def main():
    out = Path('data/context_cases_v1')
    out.mkdir(parents=True, exist_ok=True)
    paths = [out / f'{split}.jsonl' for split in ('dev', 'check')]
    if any(path.exists() for path in paths):
        raise SystemExit('기존 예제 파일이 있습니다. 새 버전 경로를 사용하세요.')
    manifest = {}
    for split, pairs, path in zip(('dev', 'check'), (DEV, CHECK), paths):
        docs = []
        for i, (category, left, right) in enumerate(pairs, 1):
            for side, text in enumerate((left, right), 1):
                docs.append(make_doc(f'CTX_{split}_{i:02}_{side}', split,
                                     category, f'{split}_{i:02}', text))
        payload = ''.join(json.dumps(d, ensure_ascii=False) + '\n' for d in docs)
        path.write_text(payload, encoding='utf-8')
        manifest[split] = dict(documents=len(docs), gold=sum(len(d['entities']) for d in docs),
                               sha256=hashlib.sha256(payload.encode()).hexdigest())
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
