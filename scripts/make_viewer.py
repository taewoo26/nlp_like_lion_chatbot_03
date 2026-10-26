"""jsonl 데이터를 눈으로 보기 좋은 HTML로 바꾼다.

- 문서 1건 = 카드 1장 (ID, 문서 유형, 템플릿, 어려운 사례 태그)
- 정답 개인정보를 라벨별 색으로 표시, 마우스를 올리면 라벨·인물 ID·표기 형태
- 위쪽 검색창으로 문서 ID, 문서 유형, 태그, 본문 단어 필터

사용법 (프로젝트 루트에서)
    python scripts/make_viewer.py data/pii_synth_finance_dev.jsonl
    → data/pii_synth_finance_dev_viewer.html 생성 → 더블클릭해서 브라우저로 열기

결과 HTML도 data/ 안에 만들어지므로 GitHub에 올라가지 않는다.
"""
import html
import json
import sys
from collections import Counter
from pathlib import Path

COLORS = {
    "person": "#ffd6d6",
    "phone_number": "#d6e8ff",
    "email": "#d9f5dc",
    "address": "#fff1c2",
    "resident_registration_number": "#f0d9ff",
    "account_number": "#ffe2c7",
    "card_number": "#c9f2ee",
    "organization": "#e3e3e3",
}


def highlight(text, entities):
    out, pos = [], 0
    for e in sorted(entities, key=lambda e: (e["start"], -e["end"])):
        s, t = e["start"], e["end"]
        if s < pos:  # 겹치는 개체는 건너뜀 (표시만 생략, 데이터는 그대로)
            continue
        out.append(html.escape(text[pos:s]))
        tip = " · ".join(str(x) for x in (e.get("label"), e.get("person_id"), e.get("form")) if x)
        color = COLORS.get(e.get("label"), "#eeeeee")
        out.append(
            f'<mark style="background:{color}" title="{html.escape(tip)}">'
            f'{html.escape(text[s:t])}<sub>{html.escape(str(e.get("label", "")))}</sub></mark>'
        )
        pos = t
    out.append(html.escape(text[pos:]))
    return "".join(out)


def main(path):
    src = Path(path)
    docs = [json.loads(l) for l in src.open(encoding="utf-8") if l.strip()]

    labels = Counter(e["label"] for d in docs for e in d.get("entities", []))
    tags = Counter(t for d in docs for t in d.get("hard_cases", []))
    types = Counter(d.get("doc_type", "") for d in docs)

    def chips(counter):
        return " ".join(
            f'<span class="chip" style="background:{COLORS.get(k, "#eee")}">{html.escape(k)} {v}</span>'
            for k, v in counter.most_common()
        )

    cards = []
    for d in docs:
        ents = d.get("entities", [])
        tag_html = "".join(f'<span class="tag">{html.escape(t)}</span>' for t in d.get("hard_cases", []))
        search = " ".join([d.get("id", ""), d.get("doc_type", ""), d.get("template", ""),
                           *d.get("hard_cases", []), d.get("text", "")]).lower()
        cards.append(f"""
<div class="card" data-search="{html.escape(search)}">
  <div class="head"><b>{html.escape(d.get("id", ""))}</b>
    <span>{html.escape(d.get("doc_type", ""))}</span>
    <span class="muted">{html.escape(d.get("template", ""))}</span>
    <span class="muted">개체 {len(ents)}개</span> {tag_html}</div>
  <pre>{highlight(d.get("text", ""), ents)}</pre>
</div>""")

    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>{html.escape(src.stem)}</title>
<style>
body{{font-family:-apple-system,"Apple SD Gothic Neo",sans-serif;margin:0;background:#f6f6f4;color:#222}}
header{{position:sticky;top:0;background:#fff;padding:12px 20px;border-bottom:1px solid #ddd;z-index:1}}
input{{width:100%;padding:8px;font-size:15px;box-sizing:border-box;margin-top:8px}}
.chip,.tag{{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;margin:2px}}
.tag{{background:#333;color:#fff}}
main{{padding:16px 20px;max-width:960px;margin:auto}}
.card{{background:#fff;border:1px solid #ddd;border-radius:8px;margin-bottom:14px;padding:12px 16px}}
.head{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:14px}}
.muted{{color:#888}}
pre{{white-space:pre-wrap;font-family:inherit;font-size:14px;line-height:1.8;margin:10px 0 0}}
mark{{border-radius:4px;padding:1px 2px}}
sub{{font-size:10px;color:#555;margin-left:2px}}
</style></head><body>
<header><b>{html.escape(src.name)}</b> · 문서 {len(docs)}건 · 개체 {sum(labels.values())}개
<div>{chips(labels)}</div>
<div>{" ".join(f'<span class="tag">{html.escape(k)} {v}</span>' for k, v in tags.most_common())}</div>
<div class="muted" style="font-size:12px">문서 유형: {" · ".join(f"{html.escape(k)} {v}" for k, v in types.most_common())}</div>
<input id="q" placeholder="검색: 문서 ID, 문서 유형, 태그(given_only 등), 본문 단어">
</header>
<main>{"".join(cards)}</main>
<script>
document.getElementById('q').addEventListener('input', e => {{
  const q = e.target.value.toLowerCase().trim();
  document.querySelectorAll('.card').forEach(c => {{
    c.style.display = !q || c.dataset.search.includes(q) ? '' : 'none';
  }});
}});
</script></body></html>"""

    out = src.with_name(src.stem + "_viewer.html")
    out.write_text(page, encoding="utf-8")
    print(f"생성 완료: {out}  (문서 {len(docs)}건)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("사용법: python scripts/make_viewer.py data/파일이름.jsonl")
    main(sys.argv[1])
