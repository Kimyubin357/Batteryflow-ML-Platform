import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from dash import Dash, no_update
from backend.data import Repository
from frontend.app import create_dashboard
from frontend.review_form import record_key, register


class ReviewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = create_dashboard(Repository().meta())
        cls.callbacks = {entry['callback'].__wrapped__.__name__: staticmethod(entry['callback'].__wrapped__)
                         for entry in app.callback_map.values() if 'callback' in entry}

    def setUp(self):
        self.target = dict(track='demand', target='Part 92', date='2021-11-01',
                           model='7-day Moving Average', decision='reviewed', dataVersion='v1')
        self.record = dict(note='저장한 메모', decision='reviewed', dataVersion='v1', revision=3)
        app = Dash(__name__)
        self.request = Mock()
        register(app, self.request)
        self.review = next(iter(app.callback_map.values()))['callback'].__wrapped__

    def form(self, target, saved=None, trigger='selected-target', state=None):
        self.request.return_value = {'records': saved or {}}
        with patch('frontend.review_form.ctx', SimpleNamespace(triggered_id=trigger)):
            return self.review(target, 1, state)

    def test_record_lookup_failure_disables_writing_and_clears_note(self):
        self.request.side_effect = ValueError('연결 실패')
        result = self.form(self.target)
        self.assertEqual(result[:3], ('', True, True))
        self.assertTrue(result[-1]['unavailable'])

    def test_clicking_same_row_resolves_original_row_after_sorting(self):
        rows = [dict(part='Part 92', date='2021-11-01', model='7-day Moving Average', forecast=10, plan=5, gap=5),
                dict(part='Part 95', date='2021-11-01', model='7-day Moving Average', forecast=20, plan=10, gap=10)]
        view = dict(domain='demand', tab='review', dataVersion='v1', payload=dict(date='2021-11-01', model='7-day Moving Average'))
        trigger = SimpleNamespace(triggered_id='main-grid', triggered_prop_ids={'main-grid.cellClicked': 'main-grid'})
        with patch('frontend.app.ctx', trigger), patch('frontend.app.request', return_value={'trend': []}), patch('frontend.app.demand_chart', return_value={}):
            result = self.callbacks['detail'].__func__([rows[0]], {'rowId': '1', 'rowIndex': 0}, '/demand', view, rows)
        self.assertTrue(result[0])
        self.assertEqual(result[2]['target'], 'Part 95')

    def test_saved_note_restored_and_explicit_edit_unlocks(self):
        result = self.form(self.target, {record_key(self.target): self.record})
        self.assertEqual(result[:3], ('저장한 메모', True, True))
        self.assertEqual(result[4], {})
        edited = self.form(self.target, trigger='edit-review', state=result[-1])
        self.assertEqual(edited[:4], (no_update, False, False, '수정 내용 저장'))
        self.assertEqual(edited[-1]['revision'], 3)

    def test_other_part_date_model_and_close_reset_note(self):
        saved = {record_key(self.target): self.record}
        for changes in ({'target': 'Part 93'}, {'date': '2021-11-02'}, {'model': 'LSTM'}):
            result = self.form({**self.target, **changes}, saved)
            self.assertEqual(result[:3], ('', False, False))
            self.assertEqual(result[-1]['revision'], 0)
        self.assertEqual(self.form(None)[:3], ('', True, True))

    def test_old_version_preserves_note_but_requires_reconfirmation(self):
        result = self.form({**self.target, 'dataVersion': 'v2'}, {record_key(self.target): self.record})
        self.assertEqual(result[:3], ('저장한 메모', False, False))

    def test_prepare_keeps_revision_seen_when_drawer_opened(self):
        state = dict(target=self.target, revision=3, editable=True)
        with patch('frontend.app.ctx', SimpleNamespace(triggered_id='prepare-review')), patch('frontend.app.request', return_value={'key': record_key(self.target), 'dataVersion': 'v1'}) as request:
            result = self.callbacks['prepare'].__func__(1, 0, 0, 0, 0, self.target, '수정', None, None, None, None, {'dataVersion': 'v1'}, state)
        self.assertEqual(result[2]['expectedRevision'], 3)
        self.assertEqual(request.call_count, 1)

    def test_save_success_closes_and_clears_but_failure_preserves_draft(self):
        with patch('frontend.app.ctx', SimpleNamespace(triggered_id='confirm-save')):
            with patch('frontend.app.request', return_value={'key': record_key(self.target), **self.record}):
                result = self.callbacks['save'].__func__(1, '/demand', self.target, {})
                self.assertEqual(result[2:], (False, None, None))
            with patch('frontend.app.request', side_effect=ValueError('충돌')):
                result = self.callbacks['save'].__func__(1, '/demand', self.target, {})
                self.assertEqual(result[2:], (no_update, no_update, no_update))

    def test_cancel_clears_pending_without_saving(self):
        with patch('frontend.app.ctx', SimpleNamespace(triggered_id='cancel-save')), patch('frontend.app.request') as request:
            result = self.callbacks['prepare'].__func__(1, 0, 1, 0, 0, self.target, '초안', None, None, None, None, {}, {})
        self.assertEqual(result, (False, no_update, None))
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
