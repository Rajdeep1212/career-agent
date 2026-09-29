"""Email attachments: one allow-list (.pdf, .docx) for storing, attaching and sending; a stored file
is found by its name inside the current upload folder and re-checked before any send; legacy .doc and
.txt rows are refused cleanly and never deleted."""
import base64
import email
import io
import os
import sqlite3
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import docx_fixtures as fixtures
from app import main
from app.services import email_send_boundary, gmail_service
from app.storage import attachment_store, email_store
from test_security_regressions import LOCAL, _IsolatedApp, _pdf

DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
MB = 1024 * 1024


def over_limit_pdf(limit: int = 5 * MB) -> bytes:
    """A real, parseable PDF CV just over `limit` bytes (a comment after %%EOF)."""
    content = _pdf('BIG CANDIDATE\nB.Tech 2025\nPython SQL')
    return content + b'\n%' + b'x' * (limit - len(content))


def over_limit_docx() -> bytes:
    """A real .docx CV just over the 5 MB attachment limit (an uncompressed image-sized part)."""
    target = io.BytesIO(fixtures.plain_cv())
    with zipfile.ZipFile(target, 'a', zipfile.ZIP_STORED) as out:
        out.writestr('word/media/photo.bin', os.urandom(attachment_store.MAX_BYTES))
    return target.getvalue()


class _AttachmentApp(_IsolatedApp):
    def setUp(self):
        super().setUp()
        patcher = patch.object(email_store, 'DB_PATH', self.directory / 'agent.sqlite3')
        patcher.start()
        self.addCleanup(patcher.stop)
        attachment_store.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    def legacy_row(self, name: str, content: bytes, *, stored_dir: Path | None = None, mime='application/octet-stream') -> str:
        """A row written before this fix (old allow-list, old folder), bypassing save_attachment."""
        attachment_store._ensure()
        attachment_id = 'legacy' + str(len(list(attachment_store.UPLOAD_DIR.iterdir())))
        stored_name = attachment_id + Path(name).suffix
        (attachment_store.UPLOAD_DIR / stored_name).write_bytes(content)
        stored_path = (stored_dir or attachment_store.UPLOAD_DIR) / stored_name
        with closing(sqlite3.connect(attachment_store.DB_PATH)) as conn, conn:
            conn.execute('INSERT INTO attachments (id, original_name, stored_path, mime_type, size_bytes) '
                         'VALUES (?, ?, ?, ?, ?)', (attachment_id, name, str(stored_path), mime, len(content)))
        return attachment_id

    def attached(self, attachment_id: str) -> email.message.Message:
        raw = base64.urlsafe_b64decode(gmail_service.build_message('hr@example.org', 'Hi', 'Body', attachment_id)['raw'])
        parts = [part for part in email.message_from_bytes(raw).walk() if part.get_filename()]
        self.assertEqual(len(parts), 1)
        return parts[0]


class AllowListTests(_AttachmentApp):
    def test_one_allow_list_drives_types_and_mime(self):
        self.assertEqual(attachment_store.ATTACHMENT_TYPES, {'.pdf': 'application/pdf', '.docx': DOCX})
        for drifting in ('ALLOWED_EXTENSIONS', 'MIME_TYPES'):
            self.assertFalse(hasattr(attachment_store, drifting), drifting)
        self.assertEqual(attachment_store.MAX_BYTES, 5 * MB)

    def test_save_accepts_pdf_and_docx_with_their_fixed_mime(self):
        self.assertEqual(attachment_store.save_attachment('cv.pdf', _pdf())['mime_type'], 'application/pdf')
        self.assertEqual(attachment_store.save_attachment('CV.DOCX', fixtures.plain_cv())['mime_type'], DOCX)

    def test_save_and_post_attachments_refuse_other_types(self):
        for name in ('cv.doc', 'notes.txt', 'cv.pdf.html', 'cv.docm'):
            with self.assertRaisesRegex(ValueError, 'PDF or .docx', msg=name):
                attachment_store.save_attachment(name, b'content')
            response = self.client.post('/attachments', files={'file': (name, b'content')}, headers=LOCAL)
            self.assertEqual(response.status_code, 400, name)
            self.assertIn('PDF or .docx', response.json()['detail'])
        self.assertEqual(list(attachment_store.UPLOAD_DIR.iterdir()), [], 'nothing refused is written')


class BuildMessageTests(_AttachmentApp):
    def test_a_saved_pdf_and_docx_are_sent_with_the_allow_list_mime(self):
        pdf = attachment_store.save_attachment('my_cv.pdf', _pdf())
        self.assertEqual(self.attached(pdf['id']).get_content_type(), 'application/pdf')
        docx = attachment_store.save_attachment('my_cv.docx', fixtures.plain_cv())
        self.assertEqual(self.attached(docx['id']).get_content_type(), DOCX)

    def test_the_mime_column_is_not_trusted(self):
        attachment_id = self.legacy_row('my_cv.pdf', _pdf(), mime='text/html')
        self.assertEqual(self.attached(attachment_id).get_content_type(), 'application/pdf')

    def test_a_row_from_the_old_folder_is_found_by_name_in_the_current_upload_folder(self):
        old_folder = self.directory / 'old_checkout' / 'data' / 'uploads'
        attachment_id = self.legacy_row('my_cv.pdf', _pdf(), stored_dir=old_folder)
        self.assertFalse(old_folder.exists())
        self.assertEqual(self.attached(attachment_id).get_filename(), 'my_cv.pdf')

    def test_a_file_outside_the_upload_folder_is_never_read(self):
        outside = self.directory / 'outside'
        outside.mkdir()
        (outside / 'secret.pdf').write_bytes(b'%PDF-SENTINEL')
        attachment_store._ensure()
        for stored in (outside / 'secret.pdf', attachment_store.UPLOAD_DIR / '..' / 'outside' / 'secret.pdf'):
            with closing(sqlite3.connect(attachment_store.DB_PATH)) as conn, conn:
                conn.execute('INSERT OR REPLACE INTO attachments (id, original_name, stored_path, mime_type, size_bytes) '
                             "VALUES ('escape', 'secret.pdf', ?, 'application/pdf', 13)", (str(stored),))
            with self.assertRaisesRegex(attachment_store.AttachmentRejectedError, 'missing'):
                gmail_service.build_message('hr@example.org', 'Hi', 'Body', 'escape')

    def test_names_with_separators_or_dots_are_refused(self):
        for name in ('a/b.pdf', 'a\\b.pdf', '..', '.', '', 'C:b.pdf', '../b.pdf'):
            with self.assertRaisesRegex(attachment_store.AttachmentRejectedError, 'upload folder', msg=repr(name)):
                attachment_store._upload_file(name)

    def test_a_symlink_out_of_the_upload_folder_is_refused(self):
        outside = self.directory / 'secret.pdf'
        outside.write_bytes(_pdf())
        link = attachment_store.UPLOAD_DIR / 'link.pdf'
        try:
            os.symlink(outside, link)
        except OSError:
            self.skipTest('this Windows account cannot create symlinks')
        with self.assertRaisesRegex(attachment_store.AttachmentRejectedError, 'outside the upload folder'):
            attachment_store._upload_file('link.pdf')

    def test_a_file_grown_past_the_limit_on_disk_is_refused(self):
        saved = attachment_store.save_attachment('my_cv.pdf', _pdf())
        with open(saved['stored_path'], 'ab') as stored:
            stored.write(b'x' * attachment_store.MAX_BYTES)
        with self.assertRaisesRegex(attachment_store.AttachmentRejectedError, 'larger than 5 MB'):
            gmail_service.build_message('hr@example.org', 'Hi', 'Body', saved['id'])

    def test_legacy_doc_and_txt_are_refused_and_kept(self):
        for name in ('old_cv.doc', 'notes.txt', 'tool.exe'):
            attachment_id = self.legacy_row(name, b'legacy content')
            with self.assertRaisesRegex(attachment_store.AttachmentRejectedError, rf'{name}.*PDF or \.docx'):
                gmail_service.build_message('hr@example.org', 'Hi', 'Body', attachment_id)
            self.assertIsNotNone(attachment_store.get_attachment(attachment_id), 'the row is kept')
        self.assertEqual(len(list(attachment_store.UPLOAD_DIR.iterdir())), 3, 'the files are kept')

    def test_a_deleted_row_is_refused(self):
        with self.assertRaisesRegex(attachment_store.AttachmentRejectedError, 'no longer exists'):
            gmail_service.build_message('hr@example.org', 'Hi', 'Body', 'gone')


class SendBoundaryTests(_AttachmentApp):
    def approved_draft(self, attachment_id):
        draft = email_store.create_draft('hr@example.org', 'Application', 'Review me', attachment_id)
        email_store.approve_draft(draft['id'])
        return draft['id']

    def test_a_legacy_draft_is_refused_before_it_is_claimed_and_stays_approved(self):
        for name in ('old_cv.doc', 'notes.txt'):
            draft_id = self.approved_draft(self.legacy_row(name, b'legacy content'))
            gmail = Mock(return_value={'id': 'must-not-send'})
            with patch.object(main, 'send_approved_email', gmail):
                response = self.client.post(f'/email/drafts/{draft_id}/send', headers=LOCAL)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn(name, response.json()['detail'])
            self.assertIn('PDF or .docx', response.json()['detail'])
            gmail.assert_not_called()
            self.assertEqual(email_store.get_draft(draft_id)['status'], 'approved', 'not locked as uncertain')
        self.assertEqual(len(list(attachment_store.UPLOAD_DIR.iterdir())), 2)

    def test_approve_and_send_refuses_before_approving(self):
        draft = email_store.create_draft('hr@example.org', 'Application', 'Review me',
                                         self.legacy_row('old_cv.doc', b'legacy content'))
        gmail = Mock()
        with self.assertRaises(attachment_store.AttachmentRejectedError):
            email_send_boundary.approve_and_send_draft(draft['id'], sender=gmail)
        gmail.assert_not_called()
        self.assertEqual(email_store.get_draft(draft['id'])['status'], 'draft')

    def test_a_valid_attachment_still_sends(self):
        draft_id = self.approved_draft(attachment_store.save_attachment('my_cv.pdf', _pdf())['id'])
        gmail = Mock(return_value={'id': 'gmail-1'})
        with patch.object(main, 'send_approved_email', gmail):
            response = self.client.post(f'/email/drafts/{draft_id}/send', headers=LOCAL)
        self.assertEqual(response.status_code, 200, response.text)
        gmail.assert_called_once()


class DraftReferenceTests(_AttachmentApp):
    def test_drafts_refuse_a_missing_or_disallowed_attachment(self):
        legacy = self.legacy_row('old_cv.doc', b'legacy content')
        for attachment_id, expected in (('does-not-exist', 'no longer exists'), (legacy, 'PDF or .docx')):
            response = self.client.post('/email/drafts', headers=LOCAL, json={
                'recipient': 'hr@example.org', 'subject': 'Hi', 'body': 'Body', 'attachment_id': attachment_id})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn(expected, response.json()['detail'])
            response = self.client.post('/agent/prepare-email', headers=LOCAL, json={
                'recipient': 'hr@example.org', 'company': 'ExampleCo', 'title': 'Analyst', 'attachment_id': attachment_id})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn(expected, response.json()['detail'])
        self.assertEqual(email_store.list_drafts(), [])

    def test_a_draft_shows_its_attachment_name(self):
        saved = attachment_store.save_attachment('my_cv.docx', fixtures.plain_cv())
        response = self.client.post('/email/drafts', headers=LOCAL, json={
            'recipient': 'hr@example.org', 'subject': 'Hi', 'body': 'Body', 'attachment_id': saved['id']})
        self.assertEqual(response.status_code, 200, response.text)
        draft = self.client.get(f"/email/drafts/{response.json()['id']}").json()
        self.assertEqual(draft['attachment_name'], 'my_cv.docx')
        self.assertEqual(self.client.get('/email/drafts').json()[0]['attachment_name'], 'my_cv.docx')


class CvUploadSizeTests(_AttachmentApp):
    def test_an_over_limit_cv_changes_nothing(self):
        baseline = self.client.get('/profile/current').json()
        for name, content, kind in (('big_cv.pdf', over_limit_pdf(), 'application/pdf'),
                                    ('big_cv.docx', over_limit_docx(), DOCX)):
            self.assertGreater(len(content), attachment_store.MAX_BYTES)
            self.assertLess(len(content), attachment_store.MAX_BYTES + MB)
            response = self.client.post('/cv/upload', files={'file': (name, content, kind)}, headers=LOCAL)
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(response.json()['detail'], 'CV is too large to attach: max 5 MB')
            self.assertEqual(self.client.get('/profile/current').json(), baseline, name)
        self.assertEqual(list(attachment_store.UPLOAD_DIR.iterdir()), [])

    def test_the_message_follows_the_constant(self):
        with patch.object(attachment_store, 'MAX_BYTES', 1 * MB):
            response = self.client.post('/cv/upload', headers=LOCAL,
                                        files={'file': ('cv.pdf', over_limit_pdf(MB), 'application/pdf')})
        self.assertEqual(response.json()['detail'], 'CV is too large to attach: max 1 MB')

    def test_a_cv_within_the_limit_still_updates_the_profile_and_is_stored(self):
        response = self.client.post('/cv/upload', files={'file': ('my_cv.docx', fixtures.table_cv(), DOCX)}, headers=LOCAL)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get('/profile/current').json()['name'], 'Rohan Das')
        self.assertEqual(len(list(attachment_store.UPLOAD_DIR.iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
