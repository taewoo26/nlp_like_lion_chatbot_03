import pytest

from detection.iban import iban_spans


@pytest.mark.parametrize('value', [
    'DE89370400440532013000', 'DE89 3704 0044 0532 0130 00',
    'GB29 NWBK 6016 1331 9268 19', 'NL91 ABNA 0417 1643 00',
    'NO93 8601 1117 947', 'FR14 2004 1010 0505 0001 3M02 606',
    'de00 3704 0044 0532 0130 00',  # 체크섬이 달라도 가명처리 대상
])
def test_complete_iban_does_not_consume_following_prose(value):
    text = f'IBAN: {value} confirmed / 다음 필드'
    spans = iban_spans(text)
    assert len(spans) == 1
    assert spans[0].text == value
    assert spans[0].start == 6
    assert spans[0].end == 6 + len(value)


@pytest.mark.parametrize('value', [
    'DE8937040044053201300', 'DE893704004405320130000',
    'prefixDE89370400440532013000', 'DE89370400440532013000suffix',
    'DE89370400440532013000@example.com',
    'DE89 3704 0044\n0532 0130 00',
    'ZZ89370400440532013000', '접수번호: AB12 1234',
])
def test_unconfirmed_or_embedded_identifier_is_not_an_iban(value):
    assert iban_spans(value) == []


def test_two_accounts_remain_separate():
    text = 'DE89 3704 0044 0532 0130 00 / GB29 NWBK 6016 1331 9268 19'
    assert [s.text for s in iban_spans(text)] == text.split(' / ')
