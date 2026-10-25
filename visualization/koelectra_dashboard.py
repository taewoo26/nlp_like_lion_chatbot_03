"""Read-only span explorer.

Run from any directory:
    python -m streamlit run visualization/koelectra_dashboard.py
Only saved dev files are read; no detector/model modules are imported.
"""
from dataclasses import dataclass
import csv
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import run_compare as scorer


@dataclass(frozen=True)
class Source:
    """Register another detector here without changing the rendering/matcher."""
    name: str
    gold: Path
    predictions: Path
    raw: Path
    errors: Path


SOURCES = {
    'KoELECTRA': Source('KoELECTRA', ROOT/'data/dev/pii_synth_finance_dev.jsonl',
        ROOT/'local_results/token_cls_baseline/dev/predictions.jsonl',
        ROOT/'local_results/token_cls_baseline/dev/raw_predictions.jsonl',
        ROOT/'local_results/token_cls_baseline/dev/eval/errors_token_cls.csv'),
    'MoAI INT8': Source('MoAI INT8', ROOT/'data/dev/pii_synth_finance_dev.jsonl',
        ROOT/'local_results/moai_int8/dev/predictions.jsonl',
        ROOT/'local_results/moai_int8/dev/raw_predictions.jsonl',
        ROOT/'local_results/moai_int8/dev/eval/errors_moai.csv'),
}
STATES = {
    'TP': ('정확 탐지 · Strict TP', '#dcfce7', '#166534'),
    'BOUNDARY': ('경계 오류', '#ffedd5', '#9a3412'),
    'FN': ('미탐 · 평가 출력 없음', '#fee2e2', '#991b1b'),
    'FP': ('오탐 · 정답과 겹침 없음', '#f3e8ff', '#6b21a8'),
    'LABEL': ('라벨 오류', '#fce7f3', '#9d174d'),
    'MATCH': ('1:1 매칭 실패 / 인접 개체 병합', '#dbeafe', '#1e40af'),
    'RAW': ('원시 예측 · 평가 지표와 별개', '#ccfbf1', '#115e59'),
}


def match(gold, predictions, strict=False):
    """Preserve scorer.count's label, gold and prediction iteration order."""
    result = {}
    for lab in scorer.LABELS:
        used = set()
        for gi, g in enumerate(gold):
            if g['label'] != lab:
                continue
            for pi, p in enumerate(predictions):
                if pi in used or p['label'] != lab:
                    continue
                valid = scorer.key(g) == scorer.key(p) if strict else scorer.overlap(g, p)
                if valid:
                    used.add(pi)
                    result[gi] = pi
                    break
    if len(result) != len(set(result.values())):
        raise ValueError('예측 중복 매칭')
    return result


def annotate(gold, predictions):
    strict, partial = match(gold, predictions, True), match(gold, predictions)
    owner = {pi: gi for gi, pi in partial.items()}
    gs, ps = [], []
    for gi, g in enumerate(gold):
        hits = [pi for pi, p in enumerate(predictions) if scorer.overlap(g, p)]
        same = [pi for pi in hits if predictions[pi]['label'] == g['label']]
        state = ('TP' if gi in strict else 'MATCH' if same and gi not in partial else
                 'BOUNDARY' if same else 'LABEL' if hits else 'FN')
        merged = [pi for pi in same if sum(scorer.overlap(x, predictions[pi]) for x in gold) > 1]
        gs.append(dict(g, index=gi, state=state, strict=gi in strict, partial=gi in partial,
                       overlaps=hits, merged=merged, owners={pi: owner.get(pi) for pi in hits}))
    for pi, p in enumerate(predictions):
        hits = [gi for gi, g in enumerate(gold) if scorer.overlap(g, p)]
        same = [gi for gi in hits if gold[gi]['label'] == p['label']]
        state = ('TP' if pi in strict.values() else 'MATCH' if len(hits) > 1 else
                 'BOUNDARY' if same else 'LABEL' if hits else 'FP')
        ps.append(dict(p, index=pi, state=state, strict=pi in strict.values(),
                       partial=pi in owner, overlaps=hits, owner=owner.get(pi),
                       strict_fp=pi not in strict.values(), partial_fp=pi not in owner))
    return gs, ps


def validate_entities(text, entities, allowed=None):
    seen = set()
    for e in entities:
        s, t = e['start'], e['end']
        if type(s) is not int or type(t) is not int or not 0 <= s < t <= len(text):
            raise ValueError('원문 좌표 범위 오류')
        if e.get('text') != text[s:t] or (allowed is not None and e['label'] not in allowed):
            raise ValueError('라벨 또는 원문 slice 오류')
        if scorer.key(e) in seen:
            raise ValueError('중복 span')
        seen.add(scorer.key(e))


def load_source(source):
    def indexed(path):
        rows = scorer.load_jsonl(path)
        if len(rows) != len({r['id'] for r in rows}):
            raise ValueError(f'중복 문서 ID: {path}')
        return {r['id']: r for r in rows}
    gold, pred, raw = indexed(source.gold), indexed(source.predictions), indexed(source.raw)
    if not gold or set(gold) != set(pred) or set(gold) != set(raw):
        raise ValueError('정답·예측·원시 예측의 문서 ID가 다릅니다.')
    with source.errors.open(encoding='utf-8-sig', newline='') as f:
        errors = list(csv.DictReader(f))
    required = {'doc_id','error_type','label','gold_text','pred_text'}
    if any(not required <= r.keys() or r['doc_id'] not in gold for r in errors):
        raise ValueError('오류 CSV 스키마 불일치')
    documents = {}
    for doc_id, d in gold.items():
        text, g, p, r = d['text'], d['entities'], pred[doc_id]['entities'], raw[doc_id]['entities']
        # run_compare's compact JSONL schema does not require redundant text.
        p = [dict(e, text=e.get('text', text[e['start']:e['end']])) for e in p]
        validate_entities(text, g, scorer.LABELS)
        validate_entities(text, p, scorer.LABELS)
        validate_entities(text, r)
        gs, ps = annotate(g, p)
        documents[doc_id] = dict(id=doc_id, text=text, gold=gs, predictions=ps,
            raw=[dict(e,index=i,state='RAW') for i,e in enumerate(r)],
            tokens=raw[doc_id].get('tokens', []), ms=pred[doc_id].get('ms'),
            errors=[e for e in errors if e['doc_id']==doc_id])
    scoring_docs = [dict(d, gold=d['entities']) for d in gold.values()]
    metrics, counts = scorer.summarize(scoring_docs, [pred[i]['entities'] for i in gold])
    for lab, c in counts.items():
        actual = [g for d in documents.values() for g in d['gold'] if g['label']==lab]
        if sum(g['strict'] for g in actual)!=c['strict_tp'] or sum(g['partial'] for g in actual)!=c['partial_tp']:
            raise ValueError('기존 채점기와 개체별 매칭 집계 불일치')
    return documents, metrics


def render_highlights(text, spans):
    """Split only at offset boundaries; preserve overlapping annotations.

    Each segment retains every covering span in its tooltip. No string search,
    strip, replacement, or boundary expansion is used. Escape all source HTML.
    """
    boundaries = sorted({0, len(text), *(e[k] for e in spans for k in ('start','end'))})
    parts = []
    for a, b in zip(boundaries, boundaries[1:]):
        content = html.escape(text[a:b])
        active = [e for e in spans if e['start'] <= a and b <= e['end']]
        if not active:
            parts.append(content)
            continue
        colors = list(dict.fromkeys(STATES[e['state']][1] for e in active))
        background = colors[0] if len(colors)==1 else 'linear-gradient(135deg,' + ','.join(colors) + ')'
        title = '\n'.join(f'#{e["index"]} {e["label"]} [{e["start"]}, {e["end"]}) '
                          f'{STATES[e["state"]][0]} | {e["text"]}' for e in active)
        border = 'border-bottom:2px dashed #b45309;' if any(e['state']=='MATCH' for e in active) else ''
        parts.append(f'<span title="{html.escape(title, quote=True)}" '
                     f'style="background:{background};color:#172033;{border}">{content}</span>')
    return ('<div class="pii-source" style="white-space:pre-wrap;overflow-wrap:anywhere;'
            'tab-size:4;line-height:1.95;font-family:monospace;padding:18px;'
            'border:1px solid #94a3b8;border-radius:8px;">'+''.join(parts)+'</div>')


def visible(e, labels, states, partial_only, gold_side):
    return ((not labels or e['label'] in labels) and (not states or e['state'] in states)
            and (not partial_only or not gold_side or not e['partial']))


def filter_documents(documents, labels, states, partial_only):
    selected = []
    for doc_id, d in documents.items():
        gold = [g for g in d['gold'] if visible(g, labels, states, partial_only, True)]
        pred = [p for p in d['predictions'] if visible(p, labels, states, False, False)]
        if partial_only:
            qualifies = bool(gold)
        else:
            qualifies = bool(gold or pred) if labels or states else True
        if qualifies:
            selected.append(doc_id)
    return sorted(selected)


def span_rows(spans, gold_side):
    rows=[]
    for e in spans:
        row = dict(번호=e['index'], 라벨=e['label'], 시작=e['start'], 끝=e['end'], 텍스트=e['text'],
                   판정=STATES[e['state']][0], Strict_TP=e['strict'], Partial_TP=e['partial'])
        row['겹치는_예측' if gold_side else '겹치는_정답'] = str(e['overlaps'])
        row['Partial_배정'] = str(e['owners'] if gold_side else e['owner'])
        if not gold_side:
            row['Strict_FP'], row['Partial_FP'] = e['strict_fp'], e['partial_fp']
        rows.append(row)
    return rows


def main():
    import streamlit as st
    st.set_page_config(page_title='Dev 탐지 분석', layout='wide')
    st.caption('저장된 결과를 읽는 대시보드 · 원문 좌표 [start, end) · 모델 실행 없음')
    source = SOURCES[st.sidebar.selectbox('탐지기', list(SOURCES))]
    st.title(f'{source.name} · Dev 개인정보 탐지 분석')
    paths = (source.gold, source.predictions, source.raw, source.errors)
    @st.cache_data(show_spinner=False)
    def cached_load(source_key, signatures):
        source = SOURCES[source_key]
        return load_source(source)
    try:
        signatures = tuple((p.stat().st_mtime_ns,p.stat().st_size) for p in paths)
        documents, metrics = cached_load(source.name, signatures)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        st.error(f'데이터 검증에 실패했습니다: {exc}')
        st.stop()
    gold_total = sum(len(d['gold']) for d in documents.values())
    failed = [(d['id'],g) for d in documents.values() for g in d['gold'] if not g['partial']]
    if source.name=='KoELECTRA' and (len(documents),gold_total,len(failed))!=(100,548,10):
        st.error('기준 데이터 검증 실패: dev 100문서·548개 정답·Partial 실패 10개가 필요합니다.')
        st.stop()
    cols=st.columns(4)
    for col,title,value in zip(cols, ('Dev 문서','Strict F1','Partial F1','Partial 실패 개체'),
                               (str(len(documents)),f'{metrics["strict_f1"]:.2%}',f'{metrics["partial_f1"]:.2%}',str(len(failed)))):
        col.metric(title,value)
    labels=st.sidebar.multiselect('개인정보 라벨',scorer.LABELS,help='선택 없음 = 전체 라벨')
    states=st.sidebar.multiselect('오류 / 탐지 유형',list(STATES)[:-1],
        format_func=lambda x:STATES[x][0],help='선택 없음 = 전체 유형. 매칭 결과는 필터 전에 전체 문서에서 계산합니다.')
    partial_only=st.sidebar.checkbox(f'Partial 실패 {len(failed)}건만 보기')
    raw_enabled=st.sidebar.checkbox('전체 원시 예측 표시')
    ids=filter_documents(documents,labels,states,partial_only)
    st.sidebar.caption(f'조건에 맞는 문서 {len(ids)} / {len(documents)}')
    if not ids:
        st.info('선택한 필터에 해당하는 문서가 없습니다.')
        st.stop()
    default=ids.index('D0002') if 'D0002' in ids else 0
    doc_id=st.sidebar.selectbox('문서 ID',ids,index=default)
    d=documents[doc_id]
    st.subheader(doc_id)
    st.caption(f'원문 {len(d["text"])}글자 · Gold {len(d["gold"])}개 · Prediction {len(d["predictions"])}개 · '
               f'Partial 실패 {sum(not g["partial"] for g in d["gold"])}개 · 저장된 처리시간 {d["ms"]:.2f}ms')
    legend=' '.join(f'<span style="background:{bg};color:{fg};padding:3px 7px;border-radius:4px;">{name}</span>'
                    for code,(name,bg,fg) in STATES.items() if code!='RAW')
    st.html(legend)
    st.caption('초록은 Strict TP입니다. 파랑·점선은 병합/1:1 실패를 표시합니다. 경계 오류·병합 예측도 Strict FP에 포함됩니다. '
               '원문 전체는 유지하고 필터에 맞는 구간만 강조합니다. 구간에 마우스를 올리면 라벨·좌표를 볼 수 있습니다.')
    gs=[g for g in d['gold'] if visible(g,labels,states,partial_only,True)]
    ps=[p for p in d['predictions'] if visible(p,labels,states,False,False)]
    if partial_only:
        related={pi for g in gs for pi in g['overlaps']}
        # Retain the full merged prediction, including its matched first name.
        ps=[p for p in d['predictions'] if p['index'] in related]
    left,right=st.columns(2)
    for col,title,spans,is_gold in ((left,'Gold',gs,True),(right,source.name+' Prediction · 평가용 7종',ps,False)):
        with col:
            st.markdown(f'### {title}')
            st.html(render_highlights(d['text'],spans))
            if spans: st.dataframe(span_rows(spans,is_gold),hide_index=True,width='stretch')
            else: st.info('이 조건에 해당하는 구간이 없습니다. 원문은 전체 표시합니다.')
    failures=[g for g in d['gold'] if not g['partial']]
    if failures:
        with st.expander('Partial 실패의 매칭 근거',expanded=partial_only):
            for g in failures:
                st.write(f'Gold #{g["index"]} · {g["label"]} · {g["text"]} [{g["start"]}, {g["end"]}) · {STATES[g["state"]][0]}')
                if g['overlaps']:
                    for pi in g['overlaps']:
                        p=d['predictions'][pi]
                        st.write(f'Prediction #{pi} → Gold #{p["owner"]}: {p["text"]!r}')
                else:
                    raw_hits=[r for r in d['raw'] if scorer.overlap(g,r)]
                    st.write('평가용 겹침 예측 없음. 원시 예측: '+str([
                        {k:r[k] for k in ('label','text','start','end')} for r in raw_hits]))
                    if raw_hits: st.caption('원시 라벨은 존재하지만 7종 매핑에서 제외됐습니다. 평가 기준에서는 미탐입니다.')
    if raw_enabled:
        st.markdown('### 전체 원시 예측 · 평가 지표와 별개')
        st.caption('프로젝트 라벨 필터와 독립적입니다. GENERIC_ID는 전화번호 정답과 겹쳐도 7종 평가에는 포함되지 않습니다.')
        raw_labels=st.multiselect('원시 라벨',sorted({r['label'] for r in d['raw']}),help='선택 없음 = 전체 원시 라벨')
        rs=[r for r in d['raw'] if not raw_labels or r['label'] in raw_labels]
        st.html(render_highlights(d['text'],rs))
        st.dataframe([{k:r[k] for k in ('index','label','text','start','end')} for r in rs],hide_index=True,width='stretch')
        with st.expander('저장된 토큰별 BIO/BIOES 예측'):
            st.dataframe(d['tokens'],hide_index=True,width='stretch')
    with st.expander('기존 오류 CSV · 이 문서'):
        st.caption('기존 CSV는 경계 오류·라벨 오류·미탐·오탐의 진단표입니다. Partial 1:1 배정과 구별해 표시하며 좌표 복원에 문자열 검색을 사용하지 않습니다.')
        st.dataframe(d['errors'],hide_index=True,width='stretch')
    with st.expander('입력 파일과 검증 기준'):
        for title,p in zip(('Gold JSONL','평가 Prediction JSONL','원시 Prediction JSONL','기존 오류 CSV'),paths):
            st.write(f'{title}: {p.relative_to(ROOT)}')
        st.write('Gold: id/text/entities(label,start,end,text). 평가 예측: id/entities/ms. 원시 예측: id/entities/unmapped_entities/tokens/windows.')
        st.write('Strict·Partial 모두 원본 순서의 1:1 매칭이며 기존 run_compare.py의 라벨별 TP 수와 검증했습니다. 화면 필터는 매칭을 변경하지 않습니다.')


if __name__=='__main__':
    main()
