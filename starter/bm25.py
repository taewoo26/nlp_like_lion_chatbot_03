"""BM25 키워드 점수 계산 (실시간 방식).

질의의 내용어(명사·동사·형용사 등)를 Kiwi로 추출하고,
후보 문서의 masked_text에서 같은 방식으로 내용어를 뽑아 BM25 점수를 매긴다.

임베딩 유사도와 합산하여 키워드가 실제로 있는 문서를 위로 올리는 용도.
고도화 때 DB에 content_terms를 사전 저장하면 실시간 추출을 없앨 수 있다.
"""
import math
import re

_TOKEN = re.compile(r"<[A-Z_]+_[0-9A-Za-z]+>")
_KEEP_TAGS = ("NNG", "NNP", "VV", "VA", "XR", "SL", "SN")


def extract_terms(text):
    """PII 토큰을 지운 뒤 Kiwi로 내용어만 뽑는다.

    반환: set of str (예: {"대출", "상담", "VOC"})
    """
    from detection.rule.kiwi_filter import _get_kiwi
    return {t.form for t in _get_kiwi().tokenize(_TOKEN.sub(" ", text))
            if t.tag in ("NNG", "NNP") or (t.tag.startswith(_KEEP_TAGS) and len(t.form) > 1)}


def has_keywords(query_terms):
    """질의에 내용어가 있는지 판단. 없으면 '전체 이력' 같은 질의."""
    return bool(query_terms)


def bm25_scores(query_terms, docs, k1=1.5, b=0.75):
    """후보 문서들의 BM25 점수를 계산한다.

    query_terms: 질의 내용어 set (extract_terms 결과)
    docs: [{"doc_id", "masked_text", ...}, ...]
    반환: {doc_id: float}  (점수가 높을수록 키워드 매칭이 좋음)

    표준 BM25 공식:
      score(D, Q) = Σ IDF(q) × TF(q, D) × (k1 + 1) / (TF(q, D) + k1 × (1 - b + b × |D| / avgdl))
      IDF(q) = log((N - n(q) + 0.5) / (n(q) + 0.5) + 1)
    """
    if not query_terms or not docs:
        return {}

    doc_terms = {}
    doc_lengths = {}
    for d in docs:
        terms = extract_terms(d["masked_text"])
        doc_terms[d["doc_id"]] = terms
        doc_lengths[d["doc_id"]] = len(terms)

    n = len(docs)
    avgdl = sum(doc_lengths.values()) / n if n else 1

    df = {}
    for q in query_terms:
        df[q] = sum(1 for terms in doc_terms.values() if q in terms)

    idf = {}
    for q in query_terms:
        idf[q] = math.log((n - df[q] + 0.5) / (df[q] + 0.5) + 1)

    scores = {}
    for d in docs:
        did = d["doc_id"]
        dl = doc_lengths[did]
        terms = doc_terms[did]
        score = 0.0
        for q in query_terms:
            tf = 1 if q in terms else 0
            if tf == 0:
                continue
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * dl / avgdl)
            score += idf[q] * numerator / denominator
        scores[did] = score
    return scores
