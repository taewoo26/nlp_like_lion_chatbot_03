"""Output routing only; shared results must never receive raw experiment data."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL_RESULTS = ROOT / 'local_results'
SHARED_RESULTS = ROOT / 'results'


def private_output(value):
    """Argparse path type: reserve results/ for reviewed summary documents."""
    path = Path(value)
    resolved = path.resolve()
    if resolved == SHARED_RESULTS or resolved.is_relative_to(SHARED_RESULTS):
        raise ValueError('results/ is reserved for shared summaries; use local_results/ for experiment output')
    return path
