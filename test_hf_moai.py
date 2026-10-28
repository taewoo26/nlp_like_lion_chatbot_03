from detection.hf.moai import MoaiDetector

if __name__ == "__main__":
    detector = MoaiDetector()

    texts = [
        "홍길동의 주민번호는 900101-1234567이고 전화번호는 010-1234-5678입니다.",
        "이메일은 hong@example.com이고 주소는 서울시 강남구 테헤란로 123입니다.",
        "카드번호 1234-5678-9012-3456으로 결제했습니다.",
    ]
    for text in texts:
        print(f"\n입력: {text}")
        spans = detector.detect(text)
        for span in spans:
            print(f"  [{span.label}] '{span.text}' ({span.start}:{span.end})")
