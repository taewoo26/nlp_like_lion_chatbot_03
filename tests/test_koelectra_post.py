import unittest
from detection.base import Span
from detection.token_cls.koelectra_post import split_person_lines


class PostTests(unittest.TestCase):
    def test_split_and_preserve_spaced_name(self):
        text = '김은영\r\n\r\n윤 선영'
        result = split_person_lines(text, [Span(0, len(text), 'person', text)])
        self.assertEqual([s.text for s in result], ['김은영', '윤 선영'])
        self.assertTrue(all(text[s.start:s.end] == s.text for s in result))

    def test_other_labels_and_particles_unchanged(self):
        text = '서울\n101호'
        span = Span(0, len(text), 'address', text)
        self.assertEqual(split_person_lines(text, [span]), [span])
        self.assertEqual(split_person_lines('승현아', [Span(0, 3, 'person', '승현아')])[0].text, '승현아')

    def test_does_not_silently_drop_false_positive_word(self):
        text = '달래\n김서연'
        self.assertEqual([s.text for s in split_person_lines(text, [Span(0, len(text), 'person', text)])], ['달래', '김서연'])


if __name__ == '__main__':
    unittest.main()
