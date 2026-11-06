"""역할 마스터 읽기 (메타데이터 규칙 정의서 v0.2 ROLE-01~07).

역할 값과 단서는 코드에 쓰지 않고 마스터에서 읽는다 (ROLE-07).
  - load_role_master(conn)  : rag.role_master 에서 읽는다 (적재 단계에서 사용)
  - load_role_master()      : DB 없이 person_role_config.json 사본을 쓴다 (단위 테스트·오프라인)

문서 단서 = doc_cues ∪ query_cues.
  질의에서 그 역할을 뜻하는 단어(예: '직원')는 문서에서 이름 앞뒤에 붙어도 같은 뜻이다.
  v0.2 employee.doc_cues 에는 '직원'이 없어서, 합치지 않으면 '직원 홍길동'이 unknown 이 된다.
"""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROLE_CODES = ("customer", "employee", "agent", "staff_contact", "guardian", "unknown")
_SEED = Path(__file__).with_name("person_role_config.json")


@dataclass(frozen=True)
class RoleEntry:
    code: str
    name_ko: str
    query_cues: tuple
    doc_cues: tuple
    field_labels: tuple
    priority: object = None
    active: bool = True


class RoleMaster:
    def __init__(self, entries):
        self.entries = {e.code: e for e in entries if e.active}
        extra = set(self.entries) - set(ROLE_CODES)
        if extra:
            raise ValueError(f"정의되지 않은 역할 코드: {sorted(extra)}")
        if "unknown" not in self.entries:
            raise ValueError("unknown 역할이 마스터에 없음")
        self.cue_to_role = self._index(lambda e: set(e.doc_cues) | set(e.query_cues), "단서")
        self.field_to_role = self._index(lambda e: {_label(f) for f in e.field_labels}, "항목명")
        self.query_cue_to_role = self._index(lambda e: set(e.query_cues), "질의 단서")

    def _index(self, words_of, what):
        out = {}
        for entry in self.entries.values():
            for word in words_of(entry):
                word = word.strip()
                if not word:
                    continue
                if out.get(word, entry.code) != entry.code:
                    raise ValueError(f"{what} '{word}'가 두 역할에 등록됨")
                out[word] = entry.code
        return out

    @classmethod
    def from_rows(cls, rows):
        """rows: (role_code, role_name_ko, query_cues, doc_cues, field_labels, priority, active)"""
        return cls(RoleEntry(code, name, tuple(q or ()), tuple(d or ()), tuple(f or ()), prio, bool(act))
                   for code, name, q, d, f, prio, act in rows)


def _label(field):
    return field.strip().rstrip(":：").strip()


@lru_cache(maxsize=1)
def _from_file():
    data = json.loads(_SEED.read_text(encoding="utf-8"))
    extra = data.get("extra_field_labels", {})
    rows = []
    for code, v in data["roles"].items():
        fields = list(v["field_labels"]) + list(extra.get(code, []))
        rows.append((code, v["role_name_ko"], v["query_cues"], v["doc_cues"], fields,
                     v.get("priority"), v.get("active", True)))
    return RoleMaster.from_rows(rows)


def load_role_master(conn=None):
    if conn is None:
        return _from_file()
    with conn.cursor() as cur:
        cur.execute("""SELECT role_code, role_name_ko, query_cues, doc_cues, field_labels, priority, active
                       FROM rag.role_master""")
        return RoleMaster.from_rows(cur.fetchall())
