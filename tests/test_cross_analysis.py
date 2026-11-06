"""One-to-one and merged-entity analysis regression tests."""
import unittest

from eval.cross_analysis import cause, merged_indices, pairs


def entity(s, e, label='person'):
    return dict(start=s, end=e, label=label)


class MatchingTests(unittest.TestCase):
    def test_merged_prediction_consumed_once(self):
        gold = [entity(0, 2), entity(3, 5)]
        predictions = [entity(0, 5)]
        strict = pairs(gold, predictions, True)
        partial = pairs(gold, predictions, False)
        self.assertEqual(strict, {})
        self.assertEqual(partial, {0: 0})
        merges = merged_indices(gold, predictions)
        self.assertEqual(merges, {0: [0, 1]})
        self.assertEqual([cause(i, gold, predictions, strict, merges) for i in range(2)],
                         ['ADJACENT_MERGE', 'ADJACENT_MERGE'])

    def test_exact_matching_also_consumes_once(self):
        self.assertEqual(pairs([entity(0, 2), entity(0, 2)], [entity(0, 2)], True), {0: 0})

    def test_label_error_not_partial_tp(self):
        gold, pred = [entity(0, 2)], [entity(0, 2, 'address')]
        self.assertEqual(pairs(gold, pred, False), {})
        self.assertEqual(cause(0, gold, pred, {}, {}), 'LABEL_ERROR')

    def test_boundary_and_missing(self):
        gold = [entity(0, 2), entity(4, 6)]
        pred = [entity(0, 3)]
        self.assertEqual(cause(0, gold, pred, {}, {}), 'BOUNDARY_ERROR')
        self.assertEqual(cause(1, gold, pred, {}, {}), 'COMPLETE_MISS')

    def test_existing_greedy_order_is_preserved(self):
        gold = [entity(0, 2), entity(3, 5)]
        pred = [entity(0, 5), entity(0, 2)]
        self.assertEqual(pairs(gold, pred, False), {0: 0})
        self.assertEqual(pairs(gold, pred, True), {0: 1})


if __name__ == '__main__':
    unittest.main()
