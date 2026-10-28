"""Production imports must work without the optional local legacy package."""
import subprocess
import sys
import unittest
from pathlib import Path


class ProductionImportTests(unittest.TestCase):
    def test_production_imports_do_not_load_legacy(self):
        code = """
import importlib.abc
import sys
class RejectLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('experiments.legacy'):
            raise ImportError('Production attempted to load local legacy code')
sys.meta_path.insert(0, RejectLegacy())
from detection.combo import ComboDetector
from detection.token_cls.koelectra import KoElectraDetector
from detection.token_cls.koelectra_post import KoElectraPostDetector
from detection.token_cls.moai import MoAIDetector
from detection.token_cls.veil import VeilDetector
from detection.moai.runtime import prepare_runtime
from eval.make_preds import load_detector
assert 'detection.token_cls.detector' not in sys.modules
assert 'detection.moai.detector' not in sys.modules
"""
        result = subprocess.run(
            [sys.executable, '-B', '-c', code],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
