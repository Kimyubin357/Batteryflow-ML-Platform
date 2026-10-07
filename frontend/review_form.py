"""Load a fresh review snapshot for each drawer selection and explicitly enable edits."""
import httpx
from dash import Input, Output, State, ctx, no_update
from frontend.components import badge, callout


def record_key(target):
    key = f"{target['track']}:{target['target']}"
    if target['track'] == 'demand':
        key += f":{target['date']}:{target['model']}"
    return key


def register(app, request):
    @app.callback(
        Output('review-note', 'value'), Output('review-note', 'readOnly'),
        Output('prepare-review', 'disabled'), Output('prepare-review', 'children'),
        Output('edit-review', 'style'), Output('review-status', 'children'),
        Output('review-state', 'data'),
        Input('selected-target', 'data'), Input('edit-review', 'n_clicks'),
        State('review-state', 'data'), prevent_initial_call=True)
    def review(target, clicks, state):
        hidden = {'display': 'none'}
        if not target:
            return '', True, True, '확인 처리', hidden, [], None
        if ctx.triggered_id == 'edit-review':
            if not clicks or not state or state.get('target') != target or state.get('unavailable'):
                return (no_update,) * 7
            return no_update, False, False, '수정 내용 저장', hidden, badge('수정 중', 'info'), {**state, 'editable': True}
        try:
            saved = request('/api/records/state')['records'].get(record_key(target), {})
        except (ValueError, httpx.HTTPError):
            return ('', True, True, '확인 처리', hidden,
                    callout('기록 조회 실패', '상세를 다시 열어 저장 상태를 확인해 주세요.', 'warning'),
                    {'target': target, 'unavailable': True})
        current = bool(saved and saved.get('decision') == 'reviewed' and saved.get('dataVersion') == target.get('dataVersion'))
        status = badge('확인 완료' if current else '재확인 필요' if saved else '미확인', 'success' if current else 'warning' if saved else 'info')
        return (saved.get('note', ''), current, current, '확인 처리', {} if current else hidden, status,
                {'target': target, 'revision': saved.get('revision', 0), 'editable': not current})
