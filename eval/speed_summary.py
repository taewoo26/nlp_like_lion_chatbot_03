"""같은 조건에서 연속 실행한 예측의 ms를 모아 평균과 중앙값을 보여줘요."""
import glob
import json
import statistics
import sys

for name in sys.argv[1:]:
    for path in sorted(glob.glob(f'results/preds/{name}_speed_run*.jsonl')):
        ms = [json.loads(l)['ms'] for l in open(path, encoding='utf-8') if l.strip()]
        print(f'{path.split("/")[-1]:48} 평균 {statistics.mean(ms):7.1f}  중앙값 {statistics.median(ms):7.1f}')