"""Run using visualization/.venv; no model dependencies or network needed."""
import unittest


class MoAIDashboardTests(unittest.TestCase):
    def test_source_switch_filters_raw_bioes(self):
        from streamlit.testing.v1 import AppTest
        from visualization.koelectra_dashboard import ROOT
        at=AppTest.from_file(str(ROOT/'visualization/koelectra_dashboard.py'),default_timeout=30).run()
        next(x for x in at.selectbox if x.label=='탐지기').select('MoAI INT8').run()
        self.assertEqual(len(at.exception),0)
        self.assertIn('MoAI INT8',at.title[0].value)
        self.assertTrue(any(x.value=='84.15%' for x in at.metric))
        at.checkbox[0].check().run()
        self.assertEqual(len(at.exception),0)
        self.assertIn('86',at.checkbox[0].label)
        at.checkbox[1].check().run()
        self.assertEqual(len(at.exception),0)
        self.assertTrue(any(x.label=='원시 라벨' for x in at.multiselect))


if __name__=='__main__':
    unittest.main()
