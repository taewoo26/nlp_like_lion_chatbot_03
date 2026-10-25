"""Regression checks for private output routing and aggregate-only sharing."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import run_compare as scorer
from eval.export_summary import prepare
from result_paths import ROOT, LOCAL_RESULTS, SHARED_RESULTS, private_output


class OutputRoutingTests(unittest.TestCase):
    def test_shared_outputs_rejected_after_normalization(self):
        for path in (SHARED_RESULTS, SHARED_RESULTS/'new/raw.jsonl',
                     LOCAL_RESULTS/'..'/'results'/'new.csv'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                private_output(path)
        self.assertEqual(private_output(LOCAL_RESULTS/'new/raw.jsonl'), LOCAL_RESULTS/'new/raw.jsonl')

    def test_clis_reject_shared_before_loading_input_or_model(self):
        target = str(SHARED_RESULTS/'must_not_be_created')
        commands = [
            ['-m', 'detection.rule.run', '--input', 'missing.jsonl', '--output', target],
            ['-m', 'detection.token_cls.run', '--input', 'missing.jsonl', '--out', target],
            ['-m', 'detection.token_cls.smoke', '--input', 'missing.jsonl', '--out', target],
            ['-m', 'detection.token_cls.analyze', '--gold', 'missing.jsonl', '--run', target],
            ['-m', 'detection.moai.run', '--input', 'missing.jsonl', '--out', target],
            ['-m', 'detection.moai.smoke', '--out', target],
            ['-m', 'detection.moai.test_reference', '--source', 'missing.py', '--out', target],
            ['-m', 'eval.cross_analysis', '--out', target],
            ['run_compare.py', '--gold', 'missing.jsonl', '--pred', 'x=missing.jsonl', '--out', target],
        ]
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, '-B', *command], cwd=ROOT, capture_output=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(b'private_output', result.stderr)
                self.assertFalse(Path(target).exists())

    @unittest.skipUnless((LOCAL_RESULTS/'token_cls_baseline/dev/predictions.jsonl').is_file(), 'Private experiment files required')
    def test_dashboard_and_discovery_use_private_copies(self):
        from eval.cross_analysis import discover
        from visualization.koelectra_dashboard import SOURCES
        _, sources, _ = discover(ROOT)
        self.assertEqual(set(sources), {'rule', 'koelectra'})
        self.assertTrue(all(path.is_relative_to(LOCAL_RESULTS) for path in sources.values()))
        for source in SOURCES.values():
            for path in (source.predictions, source.raw, source.errors):
                self.assertTrue(path.is_relative_to(LOCAL_RESULTS))
                self.assertTrue(path.is_file())


class PreservationAndSharingTests(unittest.TestCase):
    @unittest.skipUnless((LOCAL_RESULTS/'_migration/manifest.json').is_file(), 'Local migration record required')
    def test_canonical_and_original_archive_match_manifest(self):
        manifest = json.loads((LOCAL_RESULTS/'_migration/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(len(manifest['files']), 56)
        archive = LOCAL_RESULTS/'_archive/results_before_layout_20261025'
        for row in manifest['files']:
            for base in (LOCAL_RESULTS, archive):
                path = base/row['path']
                with self.subTest(path=path):
                    self.assertEqual(path.stat().st_size, row['bytes'])
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row['sha256'])

    @unittest.skipUnless((LOCAL_RESULTS/'token_cls_baseline/dev/predictions.jsonl').is_file(), 'Private experiment files required')
    def test_export_ignores_unselected_sensitive_fields(self):
        original_loads = json.loads
        sentinel = 'PRIVATE_CANARY_MUST_NEVER_BE_SHARED'
        def contaminated_loads(*args, **kwargs):
            value = original_loads(*args, **kwargs)
            if isinstance(value, dict):
                value['private_secret'] = sentinel
                if 'merged_person_predictions' in value:
                    value['merged_person_predictions'] = [{'prediction': sentinel}]
            return value
        with patch('eval.export_summary.json.loads', side_effect=contaminated_loads):
            reports = prepare(LOCAL_RESULTS, ROOT/'data/dev/pii_synth_finance_dev.jsonl')
        self.assertEqual(len(reports), 4)
        for name, body in reports.items():
            self.assertNotIn(sentinel, body)
            self.assertNotIn(str(ROOT), body)
            self.assertEqual((SHARED_RESULTS/name).read_text(encoding='utf-8'), body)

    def test_shared_tree_contains_only_summaries_without_entity_text(self):
        paths = [p for p in SHARED_RESULTS.rglob('*') if p.is_file() and p.name != '.gitkeep']
        self.assertTrue(paths)
        text = '\n'.join(p.read_text(encoding='utf-8') for p in paths)
        for path in paths:
            self.assertEqual(path.suffix, '.md')
        self.assertNotRegex(text, r'\bD\d{4}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')
        gold_path = ROOT/'data/dev/pii_synth_finance_dev.jsonl'
        if gold_path.is_file():
            gold = scorer.load_jsonl(gold_path)
            values = {e['text'] for row in gold for e in row['entities'] if len(e['text']) >= 2}
            for value in values:
                self.assertNotIn(value, text)


if __name__ == '__main__':
    unittest.main()
