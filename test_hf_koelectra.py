"""atonlee/koelectra-ko-pii-ner 빠른 동작 확인."""
from detection.hf.koelectra import KoelectraDetector

SAMPLES = [
    "안녕하세요, 저는 김민준입니다. 연락처는 010-1234-5678이고 이메일은 minjun@example.com입니다.",
    "주민등록번호 900101-1234567이고 계좌번호는 국민은행 123-456-789012입니다.",
    "서울특별시 강남구 테헤란로 123, 4층에 거주 중이며 카드번호는 1234-5678-9012-3456입니다.",
]

def main():
    print("KoELECTRA PII NER 모델 로드 중...")
    detector = KoelectraDetector()
    print("로드 완료\n")

    # 로드된 레이블 확인
    try:
        labels = detector._pipe.model.config.id2label
        print(f"모델 레이블: {labels}\n")
    except Exception:
        pass

    for i, text in enumerate(SAMPLES, 1):
        print(f"[샘플 {i}] {text}")
        spans = detector.detect(text)
        if spans:
            for s in spans:
                print(f"  → {s.label}: '{s.text}' ({s.start}:{s.end})")
        else:
            print("  → (탐지 없음)")
        print()

if __name__ == "__main__":
    main()
