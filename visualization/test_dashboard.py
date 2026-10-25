"""Stored-data and Streamlit interaction checks; never runs a model."""
from html.parser import HTMLParser
import unittest

from visualization.koelectra_dashboard import (
    ROOT, SOURCES, annotate, filter_documents, load_source, render_highlights,
)


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]

    def handle_data(self,data):
        self.parts.append(data)


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs,cls.metrics=load_source(SOURCES['KoELECTRA'])

    def test_existing_partial_results_and_filters(self):
        self.assertEqual(len(self.docs),100)
        self.assertEqual(sum(len(d['gold']) for d in self.docs.values()),548)
        failures=[g for d in self.docs.values() for g in d['gold'] if not g['partial']]
        self.assertEqual(len(failures),10)
        self.assertEqual(sum(g['state']=='MATCH' for g in failures),7)
        self.assertEqual(sum(g['state']=='FN' for g in failures),3)
        self.assertEqual(len(filter_documents(self.docs,[],[],True)),10)
        self.assertEqual(len(filter_documents(self.docs,['person'],['MATCH'],True)),7)
        self.assertEqual(filter_documents(self.docs,['phone_number'],[],True),['D0043','D0086'])

    def test_merged_prediction_keeps_original_bounds(self):
        d=self.docs['D0002']
        g=d['gold'][1]
        self.assertFalse(g['partial'])
        p=d['predictions'][g['overlaps'][0]]
        self.assertEqual((p['start'],p['end'],p['owner']),(48,57,0))
        self.assertEqual(p['text'],'김은영\n\n윤 선영')

    def test_generic_id_is_raw_only(self):
        for doc_id in ['D0043','D0086']:
            d=self.docs[doc_id]
            g=next(g for g in d['gold'] if not g['partial'])
            self.assertEqual(g['overlaps'],[])
            self.assertTrue(any(r['label']=='GENERIC_ID' and r['start']==g['start']
                                and r['end']==g['end'] for r in d['raw']))

    def test_overlapping_html_preserves_source_and_escapes(self):
        text=' <script>&\n\n가  나\t끝 '
        spans=[dict(start=1,end=13,label='person',text=text[1:13],state='MATCH',index=0),
               dict(start=10,end=16,label='address',text=text[10:16],state='LABEL',index=1)]
        rendered=render_highlights(text,spans)
        parser=TextParser();parser.feed(rendered)
        self.assertEqual(''.join(parser.parts),text)
        self.assertNotIn('<script>',rendered)
        self.assertIn('linear-gradient',rendered)

    def test_app_interactions(self):
        from streamlit.testing.v1 import AppTest
        at=AppTest.from_file(str(ROOT/'visualization/koelectra_dashboard.py'),default_timeout=30).run()
        self.assertEqual(len(at.exception),0)
        at.checkbox[0].check().run()
        self.assertEqual(len(at.exception),0)
        document=next(x for x in at.selectbox if x.label=='문서 ID')
        self.assertEqual(len(document.options),10)
        document.select('D0043').run()
        at.checkbox[1].check().run()
        self.assertEqual(len(at.exception),0)
        raw_filter=next(x for x in at.multiselect if x.label=='원시 라벨')
        self.assertIn('GENERIC_ID',raw_filter.options)
        raw_filter.select('GENERIC_ID').run()
        self.assertEqual(len(at.exception),0)
        document=next(x for x in at.selectbox if x.label=='문서 ID')
        document.select('D0086').run()
        self.assertEqual(len(at.exception),0)
        project_filter=next(x for x in at.multiselect if x.label=='개인정보 라벨')
        project_filter.select('phone_number').run()
        document=next(x for x in at.selectbox if x.label=='문서 ID')
        self.assertEqual(document.options,['D0043','D0086'])
        error_filter=next(x for x in at.multiselect if x.label=='오류 / 탐지 유형')
        error_filter.select('MATCH').run()
        self.assertEqual(len(at.exception),0)
        self.assertTrue(any('해당하는 문서가 없습니다' in x.value for x in at.info))


if __name__=='__main__':
    unittest.main()
