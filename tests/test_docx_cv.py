""".docx CVs: same text shape and limits as the PDF path, with tables and text boxes read once."""
import io
import re
import socket
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

import docx
from lxml import etree

import docx_fixtures as fixtures
from app.services import cv_parser
from app.services.cv_parser import extract_cv_text, parse_profile_from_text
from test_security_regressions import LOCAL, _IsolatedApp, _pdf

DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
ROOT = Path(__file__).resolve().parents[1]


def extract(content: bytes, suffix='.docx') -> str:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / f'cv{suffix}'
        path.write_bytes(content)
        return extract_cv_text(str(path), path.name)


def lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


class ExtractionTests(unittest.TestCase):
    def test_paragraphs_become_lines(self):
        text = extract(fixtures.plain_cv())
        self.assertEqual(lines(text),
                         ['Asha Verma', 'Education', 'B.Tech in Computer Science, 2025', 'Skills', 'Python, SQL, FastAPI'])
        self.assertEqual(text.splitlines()[0], 'Asha Verma', 'with no header, the first body line stays first')

    def test_table_cells_are_read_once_each(self):
        text = extract(fixtures.table_cv())
        self.assertIn('B.Tech in Information Technology, 2025', text)
        self.assertIn('Python, PyTorch, LangChain', text)
        self.assertIn('SQL, Docker', text)
        self.assertEqual(text.count('Skills'), 1, 'a merged cell is not repeated')

    def test_paragraphs_and_tables_keep_document_order(self):
        self.assertEqual(lines(extract(fixtures.interleaved_cv())),
                         ['Kiran Rao', 'Education', 'B.Tech in Mechanical Engineering, 2025', 'CGPA 8.1',
                          'Skills', 'Python, MATLAB', 'Projects'])

    def test_nested_tables_are_read_in_place(self):
        self.assertEqual(lines(extract(fixtures.nested_table_cv())),
                         ['Divya Iyer', 'Skills', 'Python, Pandas', 'Tableau, Excel', 'Projects'])

    def test_hyperlink_text_stays_in_its_line(self):
        self.assertIn('Portfolio: github.com/sahil-example', lines(extract(fixtures.hyperlink_cv())))

    def test_content_controls_are_read_in_place(self):
        self.assertEqual(lines(extract(fixtures.content_control_cv())), ['Neha Gupta', 'Summary', 'Projects'])

    def test_header_first_footer_last_each_once(self):
        text = extract(fixtures.header_footer_cv())
        found = lines(text)
        self.assertTrue(text.startswith('Priya Sen\n'), 'an empty first-page header adds nothing')
        self.assertEqual(found[:2], ['Priya Sen', 'priya@example.com'])
        self.assertEqual(found[-1], 'References available on request')
        self.assertEqual(text.count('Priya Sen'), 1, 'repeated section headers appear once')
        self.assertEqual(text.count('References available'), 1)
        self.assertEqual(found[2:-1], ['Education', 'B.Sc in Statistics, 2025', 'Skills', 'R, Python, SQL'])

    def test_text_box_is_read_once(self):
        text = extract(fixtures.text_box_cv())
        self.assertIn('Python, TensorFlow, RAG', text)
        self.assertEqual(text.count('Python, TensorFlow, RAG'), 1, "Word's fallback copy is not repeated")

    def test_the_parser_downstream_gets_the_same_shape_as_pdf(self):
        profile = parse_profile_from_text(extract(fixtures.table_cv()))
        self.assertEqual(profile.name, 'Rohan Das')
        self.assertEqual(profile.graduation_year, 2025)
        self.assertTrue({'Python', 'PyTorch', 'LangChain', 'SQL', 'Docker'} <= set(profile.skills))
        boxed = parse_profile_from_text(extract(fixtures.text_box_cv()))
        self.assertTrue({'Python', 'TensorFlow', 'RAG'} <= set(boxed.skills))
        headed = parse_profile_from_text(extract(fixtures.header_footer_cv()))
        self.assertEqual(headed.name, 'Priya Sen')

    def test_uppercase_extension_is_accepted(self):
        self.assertIn('Asha Verma', extract(fixtures.plain_cv(), suffix='.DOCX'))

    def test_size_and_text_limits_match_the_pdf_path(self):
        with self.assertRaisesRegex(ValueError, '10 MB'):
            extract(fixtures.plain_cv() + b'\0' * (10 * 1024 * 1024))
        document = docx.Document()
        for _ in range(250):
            document.add_paragraph('x' * 1000)
        buffer = io.BytesIO()
        document.save(buffer)
        with self.assertRaisesRegex(ValueError, '200,000 characters'):
            extract(buffer.getvalue())


class FileTypeTests(unittest.TestCase):
    def test_bad_zip_is_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, r'Cannot read this \.docx'):
            extract(fixtures.bad_zip())

    def test_a_zip_that_is_not_a_word_document_is_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, r'Cannot read this \.docx'):
            extract(fixtures.zip_without_word_document())

    def test_old_doc_format_is_rejected_clearly(self):
        with self.assertRaisesRegex(ValueError, r'\.doc files .*not supported'):
            extract(b'\xd0\xcf\x11\xe0 old binary word file', suffix='.doc')

    def test_macro_and_template_formats_are_rejected(self):
        for suffix in ('.docm', '.dotx', '.txt'):
            with self.assertRaisesRegex(ValueError, 'PDF or .docx', msg=suffix):
                extract(fixtures.plain_cv(), suffix=suffix)

    def test_content_must_match_the_extension(self):
        with self.assertRaisesRegex(ValueError, r'is a PDF'):
            extract(_pdf(), suffix='.docx')
        with self.assertRaisesRegex(ValueError, r'is a Word \.docx'):
            extract(fixtures.plain_cv(), suffix='.pdf')


class ZipSafetyTests(unittest.TestCase):
    def test_limits_are_named_constants(self):
        self.assertEqual(cv_parser.MAX_DOCX_UNPACKED_BYTES, 50 * 1024 * 1024)
        self.assertEqual(cv_parser.MAX_DOCX_ENTRIES, 1000)
        self.assertEqual(cv_parser.MAX_DOCX_COMPRESSION_RATIO, 100)
        self.assertEqual(cv_parser.MAX_DOCX_READ_BYTES, 20 * 1024 * 1024)

    def test_zip_bomb_is_refused_before_it_is_opened(self):
        with self.assertRaisesRegex(ValueError, 'too large when unpacked'):
            extract(fixtures.zip_bomb_cv())

    def test_too_many_entries_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'too many parts'):
            extract(fixtures.many_entries_cv(cv_parser.MAX_DOCX_ENTRIES + 1))

    def test_extreme_compression_ratio_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'too large when unpacked'):
            extract(fixtures.high_ratio_cv(20 * 1024 * 1024))

    def test_a_lying_size_header_does_not_unpack_the_real_content(self):
        content = fixtures.lying_size_cv(real_bytes=2 * 1024 * 1024)
        info = zipfile.ZipFile(io.BytesIO(content)).getinfo('word/document.xml')
        self.assertEqual(info.file_size, 1000, 'fixture: the header lies')
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, r'Cannot read this \.docx|too large when unpacked'):
            extract(content)
        self.assertLess(time.monotonic() - started, 5)

    def test_reading_a_part_counts_actual_bytes(self):
        archive = zipfile.ZipFile(io.BytesIO(fixtures.plain_cv()))
        real = len(archive.read('word/document.xml'))
        self.assertEqual(len(cv_parser._read_part(archive, 'word/document.xml', real)), real)
        with self.assertRaisesRegex(ValueError, 'too large when unpacked'):
            cv_parser._read_part(archive, 'word/document.xml', real - 1)


class XmlSafetyTests(unittest.TestCase):
    def test_entity_expansion_is_refused_quickly(self):
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, r'Cannot read this \.docx: .*DOCTYPE'):
            extract(fixtures.entity_expansion_cv())
        self.assertLess(time.monotonic() - started, 2)

    def test_external_entity_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / 'secret.txt'
            secret.write_text('SENTINEL-SECRET')
            with self.assertRaisesRegex(ValueError, 'DOCTYPE'):
                extract(fixtures.external_entity_cv(secret.as_uri()))

    def test_parser_resolves_no_entities(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / 'secret.txt'
            secret.write_text('SENTINEL-SECRET')
            xml = (f'<!DOCTYPE r [<!ENTITY a "{fixtures._PAYLOAD}"><!ENTITY s SYSTEM "{secret.as_uri()}">]>'
                   '<r>&a;&s;</r>').encode()
            # itertext reads the parsed tree; tostring(method='text') would expand entities itself.
            text = ''.join(etree.fromstring(xml, cv_parser.DOCX_XML_PARSER).itertext())
        self.assertNotIn('SENTINEL', text)
        self.assertNotIn(fixtures._PAYLOAD, text)

    def test_parser_makes_no_network_requests(self):
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen(1)
            server.settimeout(0.3)
            port = server.getsockname()[1]
            xml = f'<!DOCTYPE r SYSTEM "http://127.0.0.1:{port}/x.dtd"><r>&x;</r>'.encode()
            try:
                etree.fromstring(xml, cv_parser.DOCX_XML_PARSER)
            except etree.XMLSyntaxError:
                pass
            with self.assertRaises(TimeoutError):
                server.accept()


class DependencyTests(unittest.TestCase):
    def test_python_docx_is_pinned(self):
        requirements = (ROOT / 'requirements.txt').read_text()
        self.assertRegex(requirements, re.compile(r'^python-docx==1\.2\.0$', re.M))


class UploadTests(_IsolatedApp):
    def test_docx_upload_and_preview(self):
        for path, name in (('/cv/parse', 'my_cv.docx'), ('/cv/upload', 'my_cv.docx'), ('/cv/upload', 'MY_CV.DOCX')):
            response = self.client.post(path, files={'file': (name, fixtures.table_cv(), DOCX)}, headers=LOCAL)
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            profile = body['profile'] if path == '/cv/upload' else body
            self.assertEqual(profile['name'], 'Rohan Das')
            if path == '/cv/upload':
                self.assertEqual(body['attachment']['original_name'], name)

    def test_doc_and_other_types_are_rejected_with_a_clear_message(self):
        for path in ('/cv/parse', '/cv/upload'):
            doc = self.client.post(path, files={'file': ('old.doc', b'\xd0\xcf\x11\xe0', 'application/msword')}, headers=LOCAL)
            self.assertEqual(doc.status_code, 400)
            self.assertIn('.doc', doc.json()['detail'])
            for name in ('cv.txt', 'cv.docm', 'cv.dotx'):
                other = self.client.post(path, files={'file': (name, fixtures.plain_cv(), DOCX)}, headers=LOCAL)
                self.assertEqual(other.status_code, 400, name)
                self.assertIn('PDF or .docx', other.json()['detail'])

    def test_bad_or_mislabelled_files_are_a_400_not_a_crash(self):
        for path in ('/cv/parse', '/cv/upload'):
            for name, content, expected in (('cv.docx', fixtures.bad_zip(), 'Cannot read this .docx'),
                                            ('cv.docx', _pdf(), 'is a PDF'),
                                            ('cv.pdf', fixtures.plain_cv(), 'is a Word .docx')):
                response = self.client.post(path, files={'file': (name, content, DOCX)}, headers=LOCAL)
                self.assertEqual(response.status_code, 400, (path, name, response.text))
                self.assertIn(expected, response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
