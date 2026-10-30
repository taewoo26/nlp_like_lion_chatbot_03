"""합성 입력을 실제 선정 탐지기부터 치환까지 통과시키는 회귀 시험."""
import pytest

from starter import pii_pipeline as pii


@pytest.fixture(autouse=True)
def synthetic_key(monkeypatch):
    monkeypatch.setattr(pii.config, 'HMAC_KEY', 'integration-test-key-not-for-production')
    pii._key.cache_clear()
    yield
    pii._key.cache_clear()


@pytest.fixture(scope='module', autouse=True)
def local_models_required():
    from detection.token_cls import koelectra, veil
    for adapter in (koelectra, veil):
        if not all((adapter.MODEL_DIR / name).is_file() for name in adapter.FILES):
            pytest.skip('실제 탐지기 통합 시험에는 로컬 KoELECTRA·Veil 모델 파일이 필요합니다.')


@pytest.mark.parametrize('value', [
    'DE89370400440532013000',
    'DE89 3704 0044 0532 0130 00',
    'de89 3704 0044 0532 0130 00',
    'GB29 NWBK 6016 1331 9268 19',
])
def test_iban_is_one_complete_account_span(value):
    text = '계좌번호: ' + value + ' 확인'
    spans = pii.detect(text)
    assert spans == [{'label': 'account_number', 'start': 6, 'end': 6 + len(value)}]
    masked, records = pii.pseudonymize(text, spans)
    assert masked == '계좌번호: ' + records[0]['token'] + ' 확인'
    assert records[0]['original'] == value
    assert records[0]['normalized'] == value.replace(' ', '').upper()


def test_iban_document_and_query_use_same_token():
    text = '계좌번호: DE89 3704 0044 0532 0130 00'
    _, records = pii.pseudonymize(text, pii.detect(text))
    from starter.search import mask_query
    _, tokens = mask_query('DE89370400440532013000 계좌 내역 찾아줘')
    assert tokens == [records[0]['token']]


@pytest.mark.parametrize('value', ['12-34-567890-12', '11-12-123456-78', '서울 12-345678-90'])
def test_driver_license_is_deleted_without_a_mapping(value):
    text = '운전면허번호: ' + value
    masked, records = pii.pseudonymize(text, pii.detect(text))
    assert masked == '운전면허번호: [운전면허번호 비공개]'
    assert records == []
    assert pii.TOKEN_PATTERN.findall(masked) == []


@pytest.mark.parametrize('value', [
    'hong(at)example.com', 'hong[at]example(dot)com',
    'hong at example dot com', 'hong 골뱅이 example 닷 com',
    'hong 골뱅이 example 점 com',
    'hong@example(dot)com',
])
def test_obfuscated_email_has_the_same_token_as_standard_email(value):
    text = '이메일: ' + value
    masked, records = pii.pseudonymize(text, pii.detect(text))
    assert len(records) == 1
    assert records[0]['label'] == 'email'
    assert records[0]['normalized'] == 'hong@example.com'
    assert records[0]['token'] == pii.make_token('email', 'hong@example.com')
    assert masked == '이메일: ' + records[0]['token']


@pytest.mark.parametrize('value', ['G. D. Hong', 'G.D.Hong', 'H.G.D'])
def test_confirmed_initial_name_is_excluded(value):
    text = '고객명: ' + value
    masked, records = pii.pseudonymize(text, pii.detect(text))
    assert masked == text
    assert records == []


@pytest.mark.parametrize('value,region', [('청구 101동 905호', ''), ('서울 가구 101동 905호', '서울')])
def test_unverified_region_is_not_exposed_by_actual_detector(value, region):
    text = '주소: ' + value
    masked, records = pii.pseudonymize(text, pii.detect(text))
    assert masked == '주소: ' + (region + ' ' if region else '') + records[0]['token']


@pytest.mark.parametrize('value', [
    '123 Main St, Los Angeles, CA 90012',
    '42 West Avenue, New York, NY 10001',
])
def test_supported_foreign_address_is_masked_as_a_whole(value):
    text = '주소: ' + value
    spans = pii.detect(text)
    assert spans == [{'label': 'address', 'start': 4, 'end': len(text)}]
    masked, records = pii.pseudonymize(text, spans)
    assert masked == '주소: ' + records[0]['token']
    assert records[0]['original'] == value


def test_fullwidth_email_is_detected_and_uses_standard_token():
    text = '이메일: ｈｏｎｇ＠ｅｘａｍｐｌｅ．ｃｏｍ'
    masked, records = pii.pseudonymize(text, pii.detect(text))
    assert masked == '이메일: ' + pii.make_token('email', 'hong@example.com')
    assert records[0]['original'] == text[5:]


@pytest.mark.parametrize('value', ['ｈｅｌｐ＠ｅｘａｍｐｌｅ．ｃｏｍ', 'h***@example.com'])
def test_excluded_email_notation_is_preserved(value):
    text = '이메일: ' + value
    masked, records = pii.pseudonymize(text, pii.detect(text))
    assert masked == text
    assert records == []
