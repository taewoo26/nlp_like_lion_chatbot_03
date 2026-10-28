from detection.rule.detector import RuleDetector

d = RuleDetector()

tests = [
    # "고객 이메일 sy.yoon07@example.net로 발송함.",
    # "회신: ej.moon49@codebridge.example.com)",
    # "문의사항은 support@kakao.com으로 보내주세요.",
    "나는 김규리인데 내 이메일은 모오게 바로바로 :kkr_6495@naver.com이지롱롱롱",
    "무얼적어야 너가 오류가 나서 잘못된걸 찾아볼수잇을까 11045262522"
]

for text in tests:
    print(f"입력: {text}")
    for span in d.detect(text):
        print(f"  → {span.label}: [{span.start}:{span.end}] '{span.text}'")
    print()
