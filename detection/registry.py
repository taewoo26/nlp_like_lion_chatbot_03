"""탐지기 이름 → 클래스. 새 탐지기를 만들면 여기에 한 줄 추가한다.

무거운 라이브러리(onnxruntime, openai)는 해당 탐지기를 쓸 때만 불러온다.
"""
import importlib

DETECTORS = {
    # 이름: "모듈:클래스"
    "rule": "detection.rule.rule_detector:RuleDetector",
    "regex": "detection.rule.regex_detector:RegexDetector",
    "moai": "detection.token_cls.moai:MoAIDetector",
    "llm": "detection.llm.llm_detector:LLMDetector",
    # 추가 토큰 분류 모델 (transformers + torch)
    "atonlee": "detection.token_cls.hf_token:AtonleeDetector",
    "piilot": "detection.token_cls.hf_token:PiilotDetector",
    "veil": "detection.token_cls.veil_detector:VeilDetector",
    # 조합 (라벨별 분담)
    "hybrid": "detection.hybrid:HybridDetector",
    "hybrid_addr": "detection.hybrid:HybridAddrDetector",
    "hybrid_v2": "detection.hybrid:HybridV2Detector",
    "hybrid_v3": "detection.hybrid:HybridV3Detector",
    "hybrid_v4": "detection.hybrid:HybridV4Detector",
    "hybrid_v5": "detection.hybrid:HybridV5Detector",
    "hybrid_v6": "detection.hybrid:HybridV6Detector",
    # 팀원 버전 예시: "rule_jmy": "detection.rule.rule_jmy:RuleDetector",
}


def get_detector(name: str):
    if name not in DETECTORS:
        raise SystemExit(f"모르는 탐지기: {name}  (가능: {', '.join(DETECTORS)})")
    module, cls = DETECTORS[name].split(":")
    return getattr(importlib.import_module(module), cls)()
