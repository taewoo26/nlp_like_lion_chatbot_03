"""PERSON 등장 위치에 바로 붙은 명시적 역할만 추출한다."""
import json
import re
from pathlib import Path


_CONFIG = json.loads(Path(__file__).with_name("person_role_config.json").read_text(encoding="utf-8"))
ROLE_ALIASES = _CONFIG["aliases"]
FIELD_ALIASES = _CONFIG["fields"]
ROLES = frozenset(ROLE_ALIASES) | {"unknown"}
_ALIAS_TO_ROLE = {alias: role for role, aliases in ROLE_ALIASES.items() for alias in aliases}
_FIELD_TO_ROLE = {alias: role for role, aliases in FIELD_ALIASES.items() for alias in aliases}
if len(_ALIAS_TO_ROLE) != sum(map(len, ROLE_ALIASES.values())):
    raise ValueError("역할 별칭이 둘 이상의 역할에 등록됨")
if len(_FIELD_TO_ROLE) != sum(map(len, FIELD_ALIASES.values())) or not set(FIELD_ALIASES) <= set(ROLE_ALIASES):
    raise ValueError("역할 필드 설정이 잘못됨")
_ALIASES = "|".join(re.escape(alias) for alias in sorted(_ALIAS_TO_ROLE, key=lambda x: (-len(x), x)))
_FIELDS = "|".join(re.escape(alias) for alias in sorted(_FIELD_TO_ROLE, key=lambda x: (-len(x), x)))
_BEFORE = re.compile(rf"(?<![가-힣])(?P<role>{_ALIASES})[ \t]+$")
_FIELD_BEFORE = re.compile(rf"(?<![가-힣])(?P<field>{_FIELDS})[ \t]*[:：][ \t]*$")
_AFTER = re.compile(
    rf"[ \t]+(?P<role>{_ALIASES})(?=$|[^가-힣]|(?:은|는|이|가|을|를|의|에게|께|도|만)(?![가-힣]))"
)


def role_for_mention(text, start, end):
    """이름과 같은 줄에서 직접 맞닿은 역할 단서가 하나일 때만 확정한다."""
    line_before = text[:start].split("\n")[-1]
    before = _BEFORE.search(line_before)
    field = _FIELD_BEFORE.search(line_before)
    after = _AFTER.match(text[end:].split("\n")[0])
    roles = {_ALIAS_TO_ROLE[m.group("role")] for m in (before, after) if m}
    if field:
        roles.add(_FIELD_TO_ROLE[field.group("field")])
    return next(iter(roles)) if len(roles) == 1 else "unknown"
