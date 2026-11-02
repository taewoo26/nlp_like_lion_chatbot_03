"""Download only the files needed for local BGE-M3 dense inference.

Run once with network access: python -m starter.download_bge
Inference in starter.embed always uses local_files_only=True.
"""
import shutil
from pathlib import Path

from huggingface_hub import constants, snapshot_download


def main():
    cache = Path(constants.HF_HUB_CACHE)
    cache.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(cache).free < 3 * 1024**3:
        raise RuntimeError("BGE-M3 다운로드에는 모델 캐시 디스크 여유 3GB 이상이 필요합니다.")
    snapshot_download(
        repo_id="BAAI/bge-m3",
        allow_patterns=[
            "1_Pooling/*", "config.json", "config_sentence_transformers.json",
            "modules.json", "pytorch_model.bin", "sentence_bert_config.json",
            "sentencepiece.bpe.model", "special_tokens_map.json",
            "tokenizer.json", "tokenizer_config.json",
        ],
    )
    print("BGE-M3 로컬 모델 준비 완료")


if __name__ == "__main__":
    main()
