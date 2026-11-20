"""인물사전 설정별로 '전체 재적재 → 검색 질문 실행'을 차례로 돌리고 비교표를 만든다 (DB 필요).

설정 (OS 환경변수로 넘기므로 .env 를 고칠 필요가 없다. .env 는 이미 있는 값을 덮지 않음)
  끔 : PERSON_DICT=off          (인물사전 없이 이름 토큰 검색)
  v1 : PERSON_DICT=on, PERSON_RULES=v1   (기존 규칙)
  v3 : PERSON_DICT=on, PERSON_RULES=v3
설정마다 --reset 으로 rag.chunks·vault 를 지우고 다시 적재한다. 마지막 설정의 데이터가 DB 에 남는다.
  - PERSON_DICT=on 적재에는 .env 의 INGEST_DSN, off 의 --reset 에는 RESET_DSN 이 필요하다.

사용법 (레포 루트, Docker DB 켜 둔 상태)
    python eval/run_person_configs.py                          # holdout: 끔 v1 v3
    python eval/run_person_configs.py --dataset person_test    # 인물사전 테스트셋 + 질문 25개
    python eval/run_person_configs.py --only v3                # 일부 설정만
    python eval/run_person_configs.py --compare 끔 v1 v3       # 실행 없이 기존 결과 파일만 비교
    python eval/run_person_configs.py --dry-run                # 실행할 명령만 출력
"""
import argparse
import os
import subprocess
import sys
import time

DATASETS = {   # (적재 문서, 질문, 질문 정답, 결과 파일 접두어, 인물 정답)
    "holdout": ("data/holdout/holdout_labeled.jsonl", "data/holdout/holdout_retrieval_queries.jsonl",
                "data/holdout/holdout_retrieval_queries_gold.jsonl", "", "data/holdout/holdout_persons_gold.jsonl"),
    "person_test": ("data/person_test/person_dict_test_labeled.jsonl",
                    "data/person_test/person_test_retrieval_queries.jsonl",
                    "data/person_test/person_test_retrieval_queries_gold.jsonl", "pt_",
                    "data/person_test/person_dict_test_persons_gold.jsonl"),
}
CONFIGS = {
    "끔": {"PERSON_DICT": "off", "PERSON_RULES": "v1"},
    "v1": {"PERSON_DICT": "on", "PERSON_RULES": "v1"},
    "v3": {"PERSON_DICT": "on", "PERSON_RULES": "v3"},
}
RUN_NAME = {"끔": "dict_off", "v1": "dict_v1", "v3": "person_v3"}


def run(cmd, env, dry):
    print("  $", " ".join(cmd))
    if dry:
        return
    t0 = time.time()
    subprocess.run(cmd, env=env, check=True)
    print(f"    ({time.time() - t0:.0f}초)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", choices=list(DATASETS), default="holdout")
    ap.add_argument("--only", nargs="+", choices=list(CONFIGS), help="일부 설정만 (기본: 끔 v1 v3)")
    ap.add_argument("--compare", nargs="+", choices=list(CONFIGS), help="실행하지 않고 기존 결과 파일만 비교")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-k", type=int, default=20)
    a = ap.parse_args()
    data, queries, gold, prefix, persons = DATASETS[a.dataset]
    py = sys.executable
    names = a.compare or a.only or list(CONFIGS)
    runs = [f"{n}=outputs/run_{prefix}{RUN_NAME[n]}.jsonl" for n in names]
    if not a.compare:
        for name, spec in zip(names, runs):
            out = spec.split("=", 1)[1]
            env = dict(os.environ, PYTHONIOENCODING="utf-8", **CONFIGS[name])
            print(f"\n### {name}: " + ", ".join(f"{k}={v}" for k, v in CONFIGS[name].items()))
            run([py, "-m", "starter.ingest", "--reset", "--data", data], env, a.dry_run)
            run([py, "-m", "starter.run_queries", "--queries", queries, "-k", str(a.k), "--out", out], env, a.dry_run)
    print("\n### 비교")
    run([py, "eval/compare_person_runs.py", "--gold", gold, "--persons", persons, "--run", *runs, "--by-type",
         "--out", f"outputs/compare_{prefix}person_runs.json"], dict(os.environ, PYTHONIOENCODING="utf-8"), a.dry_run)


if __name__ == "__main__":
    main()
