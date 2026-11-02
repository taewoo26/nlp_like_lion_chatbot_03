# BGE-M3 Dense 격리 평가

## 설치와 실행

Python 3.13 가상환경에서 `pip install -r requirements-bge.txt`를 실행한다. 모델 파일을 저장할 공간을 3GB 이상 확보한 뒤 `python -m starter.download_bge`로 한 번 다운로드한다. 이후 추론은 `local_files_only=True`로 실행하며 임베딩 API에 접속하지 않는다.

```sh
python -m eval.run_isolated_pipeline --embed-provider local --out-dir outputs/eval_local_새이름
python -m eval.run_isolated_pipeline --embed-provider bge-m3 --out-dir outputs/eval_bge_새이름
```

두 실행은 같은 Holdout 문서·질의, 같은 탐지·가명처리와 토큰 필터를 사용한다. PostgreSQL 세션 임시 테이블에 각각 `vector(1536)`과 `vector(1024)`를 만들고, 영구 RAG·Vault 테이블과 기존 결과 파일은 변경하지 않는다. 같은 HMAC 키를 사용하는 환경에서 실행해야 한다. 새 `--out-dir`은 존재하지 않아야 한다.

일반 실행에서 BGE-M3를 사용하려면 `EMBED_PROVIDER=bge-m3`, `EMBED_MODEL=BAAI/bge-m3`, `EMBED_DIM=1024`가 필요하다. 현재 영구 `rag.chunks`는 `vector(1536)`이므로 BGE-M3를 영구 DB에 바로 적재하면 안 된다. 별도 1024차원 DB를 준비하고 전체 청크와 질의를 같은 모델로 다시 임베딩해야 한다. Vault의 HMAC 매핑은 임베딩 모델과 독립적이다.

## 동작 범위

- `sentence-transformers`의 Dense 벡터만 사용한다. `normalize_embeddings=True`로 L2 정규화하고 pgvector 코사인 거리로 검색한다.
- 모델은 프로세스당 한 번 로딩하며 문서와 질의에 같은 모델을 사용한다. 배치 크기는 `EMBED_BATCH_SIZE`(기본 8)다.
- `EMBED_MAX_TOKENS` 기본값은 8192다. 이보다 긴 입력은 묵시적으로 자르지 않고 중단한다. 실제 모델 한도가 설정값보다 작아도 중단한다.
- `EMBED_DEVICE=cpu`가 기본이다. Apple Silicon의 MPS는 PyTorch가 지원할 때만 `EMBED_DEVICE=mps`로 선택한다. MPS가 없으면 CPU로 몰래 바꾸지 않는다.
- 이번 비교는 Dense만 다룬다. BGE-M3 Sparse/ColBERT 점수나 역할 기반 재순위는 별도 실험이다.

Holdout은 이미 개발·회귀 분석에 사용되었다. 결과를 독립적인 일반화 성능으로 해석하지 않는다.
