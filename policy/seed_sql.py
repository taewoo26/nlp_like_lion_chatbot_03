"""정책 YAML → seed SQL 생성기.

policy/access_policy.yaml(정책의 단일 출처)을 읽어 policy/draft/optional/policy_seed.sql 을 만듭니다.
(DB에 정책 테이블을 두기로 할 때만 사용: policy/draft/optional/full_with_policy_tables.sql 적용 후)
YAML을 고치면 이 스크립트를 다시 실행하고, 생성된 SQL을 DB에 적용하세요.

    python -m policy.seed_sql                 # 레포 루트에서 실행
    # 기존 DB 적용 (03_policy_audit.sql 적용 후):
    docker compose -p pii_starter --env-file .env -f infra/compose.yaml exec -T db \
      psql -U admin -d pii_rag -v ON_ERROR_STOP=1 < policy/draft/optional/policy_seed.sql

같은 version으로 내용이 다른 YAML을 적용하면 SQL이 실패합니다(version을 올리라는 뜻).
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import yaml

try:
    from policy.access_policy import POLICY_PATH, load_policy
except ImportError:          # policy 폴더 안에서 직접 실행한 경우
    from access_policy import POLICY_PATH, load_policy

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "policy/draft/optional/policy_seed.sql"


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
    text = Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    load_policy(path)                         # 형식 검증(잘못된 YAML이면 여기서 실패)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    ver = raw["version"]
    v = lit(ver)

    out = [
        "-- 자동 생성 파일: 직접 수정하지 마세요. 원본은 policy/access_policy.yaml",
        "-- 생성: python -m policy.seed_sql",
        f"-- 정책 버전 {ver}, YAML sha256 {sha}",
        "BEGIN;",
        "",
        "-- 같은 version인데 내용이 다르면 중단 (version을 올려야 함)",
        "DO $$ BEGIN",
        f"  IF EXISTS (SELECT 1 FROM policy.policy_version WHERE version = {v} AND source_sha256 <> {lit(sha)}) THEN",
        f"    RAISE EXCEPTION '정책 %의 내용이 DB와 다릅니다. access_policy.yaml의 version을 올리세요.', {v};",
        "  END IF;",
        "END $$;",
        "",
        "UPDATE policy.policy_version SET is_current = false WHERE is_current AND version <> " + v + ";",
        "INSERT INTO policy.policy_version (version, updated, status, unregistered_min_level, "
        "audit_log_read_min_level, source_sha256, is_current) VALUES ("
        + ", ".join([v, lit(str(raw.get("updated"))) + "::date", lit(raw.get("status")),
                     lit(int(raw["defaults"]["unregistered_min_level"])),
                     lit(int(raw["audit_log_read_min_level"])), lit(sha), "true"]) + ")",
        "ON CONFLICT (version) DO UPDATE SET is_current = true;",
        "",
    ]

    out.append("INSERT INTO policy.levels (level, name, description) VALUES")
    out.append(",\n".join(f"  ({int(k)}, {lit(x['name'])}, {lit(x.get('description'))})"
                          for k, x in sorted(raw["levels"].items())))
    out.append("ON CONFLICT (level) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;")
    out.append("")

    out.append("-- 현재 버전 기준으로 전체 교체 (정책에서 빠진 doc_type은 미등록 = Lv2 전용이 됨)")
    out.append("DELETE FROM policy.doc_type_access;")
    out.append("INSERT INTO policy.doc_type_access (doc_type, min_level, rule, rationale, note, version) VALUES")
    out.append(",\n".join(
        f"  ({lit(t)}, {int(x['min_level'])}, {int(x['rule'])}, {lit(x['rationale'])}, {lit(x.get('note'))}, {v})"
        for t, x in sorted(raw["doc_types"].items(), key=lambda kv: (kv[1]["min_level"], kv[0]))) + ";")
    out.append("")

    out.append("DELETE FROM policy.restore_rule;")
    out.append("INSERT INTO policy.restore_rule (level, label, mode, version) VALUES")
    rules = [(int(lv), lab, mode) for lv, m in raw["restore"].items() if isinstance(lv, int)
             for lab, mode in m.items()]
    out.append(",\n".join(f"  ({lv}, {lit(lab)}, {lit(mode)}, {v})" for lv, lab, mode in sorted(rules)) + ";")
    out.append("")
    out.append("COMMIT;")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--policy", default=str(POLICY_PATH))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args()
    Path(a.out).write_text(build_sql(Path(a.policy)), encoding="utf-8")
    print(f"생성: {a.out}")


if __name__ == "__main__":
    main()
