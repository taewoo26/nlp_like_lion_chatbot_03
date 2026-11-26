"""정책 YAML → DB 초기값(seed) SQL 생성기.

policy/access_policy.yaml(초기값)을 읽어 policy/draft/07_policy_seed.sql 을 만들고,
같은 SQL을 infra/init/07_seed_demo_users.sql 의 자동 생성 구간(BEGIN/END AUTO-GENERATED 표시 사이)에도 씁니다.
그래서 새 DB는 init 만으로 정책 초기값이 들어갑니다(Holdout2 무수작업 실행 요건). yaml 을 고치면 이 명령을 다시 실행하세요.
이미 있는 DB에는 아래처럼 한 번 실행합니다.

    python -m policy.seed_sql                 # 레포 루트에서 실행
    docker compose -p pii_starter --env-file .env -f infra/compose.yaml exec -T db \
      psql -U admin -d pii_rag -v ON_ERROR_STOP=1 < policy/draft/07_policy_seed.sql

운영 중 정책의 원본은 DB이고 관리자 화면에서 편집합니다. 그래서 이 SQL은
**표가 비어 있을 때만** 값을 넣습니다. 다시 실행해도 관리자가 편집한 값을 덮어쓰지 않습니다.
(초기값을 다시 넣으려면 관리자 화면에서 편집하거나, 새 DB를 만드세요.)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml

try:
    from policy.access_policy import POLICY_PATH, load_policy
except ImportError:          # policy 폴더 안에서 직접 실행한 경우
    from access_policy import POLICY_PATH, load_policy

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "policy/draft/07_policy_seed.sql"
INIT_FILE = ROOT / "infra/init/07_seed_demo_users.sql"
BLOCK_BEGIN = "-- BEGIN AUTO-GENERATED: policy seed (python -m policy.seed_sql 이 덮어씀. 직접 수정 금지)"
BLOCK_END = "-- END AUTO-GENERATED: policy seed"
BLOCK_ANCHOR = "-- 확인 (init 로그에 출력된다)"   # 구간이 없을 때 이 줄 앞에 넣는다
SEED_USER = "seed"           # 변경 이력(user_id)에 남는 편집자. 로그인 ID로 쓰지 않는 예약값


def lit(v) -> str:
    """SQL 리터럴. None은 NULL, 문자열은 작은따옴표 이스케이프."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def build_sql(path: Path = POLICY_PATH) -> str:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    policy = load_policy(path)                # 형식 검증(잘못된 YAML이면 여기서 실패)
    ver = raw["version"]

    doc_rows = ",\n".join(
        f"      ({lit(t)}, {int(x['min_level'])}, {lit(x['category'])}, {lit(x['rationale'])})"
        for t, x in sorted(raw["doc_types"].items(), key=lambda kv: (kv[1]["min_level"], kv[0])))
    team_rows = ",\n".join(
        f"      ({lit(cd)}, {lit(name)})" for cd, name in policy.teams.items())   # YAML 순서 그대로
    rule_rows = ",\n".join(
        f"      ({lit(r.team_cd)}, {lit(r.position)}, {r.level}, {lit(r.rationale)})"
        for r in policy.team_levels)          # YAML 순서 그대로

    out = [
        "-- 자동 생성 파일: 직접 수정하지 마세요. 원본은 policy/access_policy.yaml",
        "-- 생성: python -m policy.seed_sql",
        f"-- 초기값 정책 {ver}: doc_type {len(raw['doc_types'])}종, 팀 {len(policy.teams)}개, 레벨 규칙 {len(policy.team_levels)}개",
        "-- 표가 비어 있을 때만 넣는다(관리자가 편집한 값을 덮어쓰지 않음). infra/init/04_policy.sql 적용 후 실행.",
        "BEGIN;",
        f"SET LOCAL app.user_id = {lit(SEED_USER)};",
        "",
        "DO $$ BEGIN",
        "  IF NOT EXISTS (SELECT 1 FROM policy.doc_type_level) THEN",
        "    INSERT INTO policy.doc_type_level (doc_type, min_level, category, rationale) VALUES",
        doc_rows + ";",
        "  END IF;",
        "  -- 팀을 먼저 넣는다 (team_level.team_cd → teams.team_cd 외래키)",
        "  IF NOT EXISTS (SELECT 1 FROM policy.teams) THEN",
        "    INSERT INTO policy.teams (team_cd, team_name) VALUES",
        team_rows + ";",
        "  END IF;",
        "  IF NOT EXISTS (SELECT 1 FROM policy.team_level) THEN",
        "    INSERT INTO policy.team_level (team_cd, position, level, rationale) VALUES",
        rule_rows + ";",
        "  END IF;",
        "END $$;",
        "",
        "COMMIT;",
    ]
    return "\n".join(out) + "\n"


def write_init_block(sql: str, init_file: Path = INIT_FILE) -> None:
    """init 파일의 자동 생성 구간을 sql 로 바꾼다. 구간이 없으면 BLOCK_ANCHOR 앞에 새로 넣는다.
    init 파일의 줄바꿈(LF/CRLF)은 그대로 둔다."""
    text = init_file.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    block = nl.join([BLOCK_BEGIN, *sql.rstrip("\n").split("\n"), BLOCK_END]) + nl
    if BLOCK_BEGIN in text:
        start = text.index(BLOCK_BEGIN)
        end = text.index(BLOCK_END, start) + len(BLOCK_END)
        if text[end:end + len(nl)] == nl:
            end += len(nl)
        text = text[:start] + block + text[end:]
    elif BLOCK_ANCHOR in text:
        i = text.index(BLOCK_ANCHOR)
        text = text[:i] + block + nl + text[i:]
    else:
        raise ValueError(f"{init_file}: 자동 생성 구간도, 넣을 위치({BLOCK_ANCHOR!r})도 없습니다")
    init_file.write_bytes(text.encode("utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--policy", default=str(POLICY_PATH))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--init-file", default=str(INIT_FILE), help="자동 생성 구간을 갱신할 init SQL")
    ap.add_argument("--no-init", action="store_true", help="init 파일은 건드리지 않음")
    a = ap.parse_args()
    sql = build_sql(Path(a.policy))
    Path(a.out).write_text(sql, encoding="utf-8")
    print(f"생성: {a.out}")
    if not a.no_init:
        write_init_block(sql, Path(a.init_file))
        print(f"갱신: {a.init_file} (자동 생성 구간)")


if __name__ == "__main__":
    main()
