from detection.rule.detector import RuleDetector

det = RuleDetector()
NUMBER_LABELS = {'resident_registration_number', 'card_number', 'account_number', 'phone_number'}


def found(text):
    return {(s.label, s.text) for s in det.detect(text)}


def numbers(text):
    return {(l, t) for l, t in found(text) if l in NUMBER_LABELS}


def test_rrn_formats():
    assert ('resident_registration_number', '9001011234567') in found('주민등록번호 9001011234567 확인')
    assert ('resident_registration_number', '850315 2345678') in found('주민등록번호 850315 2345678 확인')
    assert ('resident_registration_number', '850315-2345678') in found('주민등록번호 850315-2345678 확인')


def test_13_digits_with_invalid_date_is_account():
    text = '출금 계좌: 가람은행 1234567890123 연락처'
    assert ('account_number', '1234567890123') in found(text)
    assert not any(l == 'resident_registration_number' for l, _ in found(text))


def test_card_formats():
    assert ('card_number', '5100 1234 5678 9012') in found('카드번호 5100 1234 5678 9012 입니다')
    assert ('card_number', '5100123456789012') in found('카드번호 5100123456789012 입니다')


def test_account_formats():
    for acc in ['1234-567-890123', '5678-12-3456789', '123456-12-123456', '123-45-678901-2']:
        assert ('account_number', acc) in found(f'입금 계좌: 가람은행 {acc} 로 보내세요')
    assert ('account_number', '123456789012') in found('출금 계좌: 가람은행 123456789012 연락처')


def test_international_phone():
    assert ('phone_number', '+82 10-1234-5678') in found('call me at +82 10-1234-5678.')
    assert ('phone_number', '+82 2-123-4567') in found('연락처 +82 2-123-4567 입니다')


def test_decoy_numbers_not_detected():
    assert not numbers('민원번호 123456789012 접수')
    assert not numbers('고객센터 1599-0000 으로 문의')
    assert not numbers('접수번호 SB-2026-00123 일자 2026-09-21')
    assert not numbers('연락처 070-1234-5678')
    assert ('phone_number', '010-1234-5678') in numbers('연락처 010-1234-5678')


def test_address_formats():
    cases = [
        '(12345) 서울특별시 마포구 한빛로 12, 3층 (서교동)',
        '부산 해운대구 가온로 7 101동 202호',
        '경기도 수원시 팔달구 매산동 12-3',
        '대구광역시 수성구 마루길 5 새봄아파트 103동 1501호',
    ]
    for addr in cases:
        assert ('address', addr) in found(f'주소: {addr} 입니다')
        