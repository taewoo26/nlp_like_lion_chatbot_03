"""01 영역: 적재 때 탐지·가명처리 채점 (ITL01). 탐지 지표는 저장소 eval/run_compare.py 의 채점 함수를 그대로 쓴다.
기존 문서와 함께 적재해도 채점 대상은 통합 테스트 84건(정답 표시가 있는 문서)이다.

입력: <실행 폴더>/pseudo_off.jsonl (없으면 pseudo_on) — 탐지 구간·토큰
      <실행 폴더>/masked_off.jsonl (없으면 masked_on) — 실제 저장된 가명처리 본문 (rag.chunks.masked_text)
탐지와 가명처리를 나눠 본다.
- P01 탐지 성능: 정답 구간과 예측 구간 비교 → strict·partial P·R·F1, 라벨별 재현율·정밀도
- P02 원문 잔존(표준 표기) / P06 탐지 어려운 표기 잔존: **실제 가명처리 본문**에 정답 개인정보가 남았는지
      원문 그대로 / 숫자만 같은 표기(구분자만 바뀜) / 숫자 6자리 이상 일부가 남음 을 나눠 센다
      (일부만 탐지돼 나머지 숫자가 남은 경우도 잔존으로 잡힌다)
- P03 토큰 일관성: 정답 개인정보마다 실제로 붙은 토큰이 같은 값의 기대 토큰(같은 HMAC)과 같은지
- P04 과잉 가명처리: 개인정보가 없는 문서의 실제 본문에 토큰 0개
- P05 삭제 대상(운전면허번호): 실제 본문에 원문·숫자 없이 삭제 표시만 있음 (vault 매핑 없음은 권한 API 쪽에서 확인)
- P07 미지원 식별정보(여권·고객번호·사번): 원문 유지 건수 (정책 미정 → 판정 보류)
- P08 개인정보와 비슷한 값의 과잉 탐지: 정답이 아닌데 개인정보로 잡힌 설계 값의 개수
지표는 전체 기준이 기본이고 dev·check 는 보조로 함께 낸다.
"""
import collections
import re

from .common import DATA, jl_read, result_row, set_row, token_of

LOOKALIKE = ["02-2026-1124", "351-260917-44", "30-571204-88", "02-3150-7000", "1588-7240",
             "2026-1101-0042-7788", "214-86-12345", "윤슬", "도담", "하랑지점"]
TOKEN = re.compile(r"<[A-Za-z_]+_[0-9A-Za-z]+>")
DELETED = "[운전면허번호 비공개]"
UNSUP = ("passport_number", "customer_id", "employee_id")


def _digits(s):
    return re.sub(r"\D", "", s)


NUM_RUN = re.compile(r"\d[\d\s\-.]*\d")


def residue_kind(value, masked):
    """정답 개인정보 value 가 가명처리 본문 masked 에 남았는가 → None / '원문' / '표기 변형' / '일부'
    숫자는 본문의 숫자 덩어리(구분자 포함 연속 숫자)마다 비교한다: 덩어리 숫자 = 값 숫자면 표기 변형,
    6자리 이상 덩어리가 값 숫자의 일부면 '일부' (탐지가 값의 앞부분만 가려 뒷부분이 남은 경우 등)."""
    if not masked:
        return None
    if value in masked:
        return "원문"
    d = _digits(value)
    if len(d) >= 6:
        clean = TOKEN.sub(" ", masked).replace(DELETED, " ")
        runs = [_digits(x) for x in NUM_RUN.findall(clean)]
        if any(r == d for r in runs):
            return "표기 변형"
        if any(len(r) >= 6 and r in d for r in runs):
            return "일부"
    return None


def _load_masked(run_dir):
    for ph in ("off", "on"):
        p = run_dir / f"masked_{ph}.jsonl"
        if p.exists():
            by = collections.defaultdict(list)
            for r in jl_read(p):
                by[r["doc_id"]].append((r["chunk_idx"], r["masked_text"]))
            return {d: "\n".join(t for _, t in sorted(v)) for d, v in by.items()}, p.name
    return None, None


def score(run_dir, run_id):
    import eval.run_compare as rc
    path = run_dir / "pseudo_off.jsonl"
    if not path.exists():
        path = run_dir / "pseudo_on.jsonl"
    if not path.exists():
        return [], [], {"skipped": "pseudo 파일 없음"}
    labeled = jl_read(DATA / "it_docs_labeled.jsonl")
    pseudo = {r["id"]: r for r in jl_read(path)}
    masked, masked_name = _load_masked(run_dir)
    rows, sets, summary = [], [], {"pseudo": path.name, "masked": masked_name or "없음 (P02~P05 미검증)"}

    def row(sp, item, content, expected, actual, result, stage=""):
        rows.append(result_row("적재", "ITL01", sp, "", "적재", item, content, expected, actual, result, stage))

    for sp in ("전체", "dev", "check"):
        docs = [dict(d) for d in labeled if sp == "전체" or d["split"] == sp]
        for d in docs:
            d["gold"] = [e for e in d["entities"] if e["label"] in rc.LABELS]
        preds, _ = rc.load_pred(str(path), docs)
        s, counts = rc.summarize(docs, preds)
        labels = {}
        for lab in rc.LABELS:
            c = counts[lab]
            p, r, f = rc.prf(c["partial_tp"], c["pred"], c["gold"])
            labels[lab] = {"gold": c["gold"], "pred": c["pred"], "P": round(p, 3), "R": round(r, 3), "F1": round(f, 3)}
        over = collections.Counter()
        for d, P in zip(docs, preds):
            gold_spans = [(e["start"], e["end"]) for e in d["entities"]]
            for p in P:
                if any(s0 < p["end"] and p["start"] < t0 for s0, t0 in gold_spans):
                    continue
                txt = d["text"][p["start"]:p["end"]]
                hit = next((v for v in LOOKALIKE if v in txt or txt in v), None)
                over[hit or "기타"] += 1
        res_kind = collections.Counter()
        res_hard = collections.Counter()
        res_examples = []
        tok_bad, tok_n, tok_missing = [], 0, []
        no_masked = [d["id"] for d in docs if masked is not None and d["id"] not in masked]
        p04_bad, p05 = [], []
        for d in docs:
            m = masked.get(d["id"]) if masked is not None else None
            for e in d["entities"]:
                if e["label"] in UNSUP:
                    continue
                if m is not None and e["label"] != "driver_license":
                    k = residue_kind(e["text"], m)
                    if k:
                        (res_hard if e.get("variant") == "hard" else res_kind)[k] += 1
                        if len(res_examples) < 5:
                            res_examples.append(f"{d['id']} {e['label']}({k})")
                if e["label"] in rc.LABELS and e.get("variant") != "hard":
                    try:
                        want = token_of(e["label"], e["text"])
                    except ValueError:
                        want = None
                    got = {x.get("token") for x in pseudo.get(d["id"], {}).get("entities", [])
                           if x.get("label") == e["label"] and x["start"] < e["end"] and e["start"] < x["end"]}
                    got.discard(None)
                    if want and got:
                        tok_n += 1
                        if got != {want}:
                            tok_bad.append(f"{d['id']} {e['label']}")
                    else:                                  # 기대 토큰을 못 구했거나 실제 토큰이 없음 → 확인 불가
                        tok_missing.append(f"{d['id']} {e['label']}")
            if m is not None and not d["entities"] and TOKEN.search(m):
                p04_bad.append(d["id"])
            if m is not None:
                for e in d["entities"]:
                    if e["label"] == "driver_license":
                        ok = DELETED in m and e["text"] not in m and not residue_kind(e["text"], m)
                        p05.append((d["id"], ok))
        unsupported = sum(1 for d in docs for e in d["entities"] if e["label"] in UNSUP)
        summary[sp] = {"strict": [round(s["strict_p"], 3), round(s["strict_r"], 3), round(s["strict_f1"], 3)],
                       "partial": [round(s["partial_p"], 3), round(s["partial_r"], 3), round(s["partial_f1"], 3)],
                       "labels": labels, "residue": dict(res_kind), "residue_hard": dict(res_hard),
                       "token_mismatch": len(tok_bad), "over_detection": dict(over), "unsupported_kept": unsupported}
        for k, v in (("strict P", s["strict_p"]), ("strict R", s["strict_r"]), ("strict F1", s["strict_f1"]),
                     ("partial P", s["partial_p"]), ("partial R", s["partial_r"]), ("partial F1", s["partial_f1"])):
            sets.append(set_row("문서 전체 (ITL01)", sp, "적재", "P01", k, v, "", "기준선 기록"))
        for lab, v in labels.items():
            if v["gold"]:
                sets.append(set_row(f"라벨 {lab} ({v['gold']}개)", sp, "적재", "P01", "partial F1", v["F1"], "", "기준선 기록"))
        row(sp, "P01", "탐지 P·R·F1", "기록 (합격 기준 미정)",
            f"strict F1 {s['strict_f1']:.3f} · partial P {s['partial_p']:.3f} · R {s['partial_r']:.3f}", "기준선 기록")
        if masked is None:
            for item, content in (("P02", "표준 표기 원문 잔존 (실제 가명처리 본문)"), ("P03", "같은 값 = 같은 토큰"),
                                  ("P04", "개인정보 없는 문서 토큰 0개"), ("P05", "운전면허번호 삭제")):
                row(sp, item, content, "", "가명처리 본문 기록 없음", "미검증")
        else:
            n = sum(res_kind.values())
            gap = f" · 본문 없는 문서 {len(no_masked)}건 ({','.join(no_masked[:3])})" if no_masked else ""
            # 본문이 빠진 문서가 있으면 잔존 0건이어도 통과로 보지 않는다
            row(sp, "P02", "표준 표기 원문 잔존 (실제 가명처리 본문)", "0건",
                f"{n}건 {dict(res_kind) or ''}" + (f" 예: {', '.join(res_examples[:3])}" if n else "") + gap,
                "실패" if n else ("미검증" if no_masked else "통과"), "가명처리")
            row(sp, "P06", "탐지 어려운 표기 잔존", "기록", f"{sum(res_hard.values())}건 {dict(res_hard) or ''}", "기준선 기록")
            row(sp, "P03", "같은 값 = 같은 토큰 (기대 HMAC 토큰과 일치)", "불일치 0건",
                f"{len(tok_bad)}/{tok_n}건 불일치" + (f": {', '.join(tok_bad[:3])}" if tok_bad else "")
                + (f" · 토큰 확인 불가 {len(tok_missing)}건 (탐지 누락 또는 토큰 계산 불가, P02 참고)" if tok_missing else ""),
                "실패" if tok_bad else ("미검증" if tok_missing else "통과"), "가명처리")
            no_pii = [d["id"] for d in docs if not d["entities"]]
            p04_gap = [x for x in no_pii if x in no_masked]
            row(sp, "P04", "개인정보 없는 문서 토큰 0개", "0건",
                f"{len(p04_bad)}건 {','.join(p04_bad)}" + (f" · 본문 없음 {','.join(p04_gap)}" if p04_gap else ""),
                "실패" if p04_bad else ("미검증" if p04_gap else "통과"), "탐지")
            dl = [d["id"] for d in docs for e in d["entities"] if e["label"] == "driver_license"]
            if dl:
                bad = [d for d, ok in p05 if not ok]
                gap05 = [x for x in dl if x in no_masked]
                row(sp, "P05", "운전면허번호: 원문 없이 삭제 표시만", "모두 삭제",
                    f"{len(p05) - len(bad)}/{len(dl)}건" + (f" · 본문 없음 {','.join(gap05)}" if gap05 else ""),
                    "실패" if bad else ("미검증" if gap05 else "통과"), "가명처리")
        row(sp, "P07", "미지원 식별정보 원문 유지", "정책 결정 후", f"{unsupported}건", "판정 보류")
        row(sp, "P08", "과잉 탐지 값", "기록",
            f"{sum(v for k, v in over.items() if k != '기타')}건 (그 밖의 오탐 {over['기타']}건)", "기준선 기록")
    return rows, sets, summary
