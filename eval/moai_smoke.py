"""MoAI ONNX 다운로드·CPU 추론 확인. 최종 NER 탐지/채점용은 아니다.

레포 루트: python -m eval.moai_smoke
출처: https://huggingface.co/BCCard/MoAI-Privacy-Filter-INT8
"""
import argparse
import json
import os
import shutil
import tempfile
import time
from pathlib import Path


def materialize_snapshot(snapshot: Path, destination: Path) -> Path:
    """캐시의 심볼릭 링크를 실제 파일로 복사해 외부 가중치 경로를 유지한다."""
    destination.mkdir(parents=True, exist_ok=True)
    for source in snapshot.rglob("*"):
        if not source.is_file():
            continue
        target = destination / source.relative_to(snapshot)
        target.parent.mkdir(parents=True, exist_ok=True)
        if (target.is_file() and not target.is_symlink()
                and target.stat().st_size == source.stat().st_size
                and target.stat().st_mtime_ns == source.stat().st_mtime_ns):
            continue
        # 중단된 복사본을 다음 실행에서 완성된 파일로 오인하지 않도록 원자적으로 교체.
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".copy-")
        os.close(fd)
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", default="main", help="모델 버전 또는 커밋 SHA")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("--threads는 1 이상이어야 합니다.")

    import numpy as np
    import onnxruntime as ort
    from huggingface_hub import snapshot_download
    from transformers import AutoTokenizer

    print("1/3 모델 다운로드·캐시 확인 (첫 실행 시 약 1.6GB 이상)", flush=True)
    snapshot = Path(snapshot_download(
        repo_id="BCCard/MoAI-Privacy-Filter-INT8", revision=args.revision,
    ))
    print(f"모델 캐시 / 실제 revision: {snapshot}", flush=True)
    local_dir = Path(__file__).resolve().parents[1] / "models" / "moai-int8" / snapshot.name
    print(f"실제 파일 준비 (최초 복사 시 추가 디스크 공간 필요): {local_dir}", flush=True)
    model_dir = materialize_snapshot(snapshot, local_dir)
    print("2/3 토크나이저·CPU 모델 로딩", flush=True)
    start = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    options = ort.SessionOptions()
    options.intra_op_num_threads = args.threads
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(model_dir / "model_quantized.onnx"),
        sess_options=options, providers=["CPUExecutionProvider"],
    )
    print(f"로딩: {time.perf_counter() - start:.1f}초 / 실행 장치: {session.get_providers()}")

    # 직접 만든 합성 문장. 정답 데이터셋은 읽지 않는다.
    text = "김민수 고객의 연락처는 010-1234-5678입니다."
    encoded = tokenizer(text, add_special_tokens=False, return_tensors="np")
    if encoded["input_ids"].shape[1] > 1024:
        raise ValueError("이 로딩 시험은 1024토큰 이하 입력만 지원합니다.")
    feeds = {
        entry.name: np.asarray(encoded[entry.name], dtype=np.int64)
        for entry in session.get_inputs()
    }
    print("3/3 짧은 문장 추론 (워밍업 1회 + 측정 1회)", flush=True)
    session.run(["logits"], feeds)
    start = time.perf_counter()
    logits = session.run(["logits"], feeds)[0]
    elapsed = (time.perf_counter() - start) * 1000
    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    expected = (1, encoded["input_ids"].shape[1], len(config["id2label"]))
    if logits.shape != expected or not np.isfinite(logits).all():
        raise ValueError(f"예상과 다른 출력: {logits.shape}, 기대 형태: {expected}")
    print(f"입력: {text}")
    print(f"logits 형태: {logits.shape} (문서 수, 토큰 수, 클래스 수)")
    print(f"추론: {elapsed:.1f}ms / ONNX Runtime: {ort.__version__}")
    print("CPU 추론 확인 완료. 개인정보 추출은 BIOES Viterbi 디코더 연결 후 가능합니다.")


if __name__ == "__main__":
    main()
