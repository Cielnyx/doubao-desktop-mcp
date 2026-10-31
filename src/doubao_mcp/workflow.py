"""Video submission, lookup and download, with one durable send attempt per request."""
from __future__ import annotations

import re
import time

from .background import BackgroundError
from .media import MediaDriver
from .tasks import TaskStore, TaskError, VideoTaskReader
from .downloads import download_video
from .prompts import build_video_prompt, extract_user_prompt


class VideoWorkflow:
    def __init__(self, driver, store=None):
        self.driver = driver
        self.store = store or TaskStore()
        self.reader = VideoTaskReader(driver, self.store)

    def _reused(self, request_id):
        self.driver.sent = True
        try:
            return {**self.reader.query(request_id=request_id), 'reused_request':True, 'no_resend':True}
        except Exception as error:
            return {'ok':False,'code':'VIDEO_STATUS_UNAVAILABLE','request_id':request_id,
                    'submission_attempted':True,'reused_request':True,'no_resend':True,
                    'status':'submission_unknown','message':'原请求已尝试发送；通道暂不可用，请继续按原编号查询。',
                    'reason':str(error)}

    def prepare(self, prompt, image_asset_ids=None, model=None):
        current = self.driver.page('video_current')['current']
        model = current['model'] if model is None else model
        if model != current['model']:
            raise BackgroundError('VIDEO_SETTING_MISMATCH', '提示词指定模型与界面模型不一致，未改动草稿。')
        original = extract_user_prompt(model, prompt).replace('\r\n','\n').replace('\r','\n')
        effective = build_video_prompt(model, original)
        result = MediaDriver(self.driver).prepare_prompt(effective, image_asset_ids or [], source_prompt=original)
        return {**result, 'user_prompt':original, 'effective_prompt':effective,
                'prompt_prefix_applied':True, 'model':model}

    def submit(self, request_id, prompt, model, ratio, duration, image_asset_ids=None, wait_seconds=15):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000 or '\0' in prompt:
            raise BackgroundError('INVALID_PROMPT', '完整视频提示词必须为1–20000字符，不能包含空字符。')
        if not isinstance(model, str) or not model.strip() or len(model) > 100:
            raise BackgroundError('INVALID_VIDEO_MODEL', 'model 必须是已查询和设置的实际模型名称。')
        ratio = 'auto' if ratio == '自动' else ratio
        if not isinstance(ratio, str) or not re.fullmatch(r'auto|[1-9]\d?:[1-9]\d?', ratio):
            raise BackgroundError('INVALID_VIDEO_RATIO', 'ratio 必须为 auto 或16:9等实际画面比例。')
        if type(duration) is not int or not 1 <= duration <= 300:
            raise BackgroundError('INVALID_VIDEO_DURATION', '视频秒数必须为整数，并符合当前模型选项。')
        if type(wait_seconds) is not int or not 0 <= wait_seconds <= 30:
            raise BackgroundError('INVALID_TIMEOUT', 'wait_seconds 必须为0–30秒。')
        assets = image_asset_ids if image_asset_ids is not None else []
        if not isinstance(assets, list) or len(assets) > 10 or any(not isinstance(a,str) or not a for a in assets) or len(set(assets)) != len(assets):
            raise BackgroundError('INVALID_IMAGE_ASSETS', 'image_asset_ids 必须为至多10个不重复的真实上传标识。')
        prompt = extract_user_prompt(model.strip(), prompt).replace('\r\n','\n').replace('\r','\n')
        payload = {'prompt':prompt, 'draft_text':prompt, 'model':model.strip(), 'ratio':ratio,
                   'duration':duration, 'image_asset_ids':assets}
        try:
            existing = self.store.get(request_id)
        except TaskError as error:
            if error.code != 'REQUEST_NOT_FOUND':
                raise
            existing = None
        if existing:
            reserved = self.store.reserve(request_id, payload)
            if reserved['record'].get('submission_attempted'):
                return self._reused(request_id)
        before = self.driver.page('snapshot')
        reserved = self.store.reserve(request_id, payload, before.get('conversation_id'),
                                      [m['id'] for m in before.get('messages',[])])
        if reserved['record'].get('submission_attempted'):
            return self._reused(request_id)
        if reserved['record'].get('conversation_id') != before.get('conversation_id'):
            raise BackgroundError('REQUEST_CONTEXT_CHANGED', '请求已保存在其他对话，未在当前对话发送。', request_id=request_id)
        current = self.driver.page('video_current')['current']
        if current != {key:payload[key] for key in ('model','ratio','duration')}:
            raise BackgroundError('VIDEO_SETTING_MISMATCH', '界面视频参数与提交要求不一致，未改动提示词。')
        effective_prompt = build_video_prompt(payload['model'], prompt)
        media = MediaDriver(self.driver)
        input_context = media.inspect()['context']
        media.prepare_prompt(effective_prompt, assets, source_prompt=prompt)
        arguments = {'prompt':effective_prompt, 'asset_ids':assets, 'model':payload['model'],
                     'ratio':ratio, 'duration':duration}
        check = self.driver.page('video_preflight', **arguments)
        if not isinstance(check, dict) or not check.get('verified') or not isinstance(check.get('proof'), dict) or not check['proof']:
            raise BackgroundError('VIDEO_PREFLIGHT_UNVERIFIED', '提交前核验未返回完整证据，未发送。', request_id=request_id)
        if check.get('conversation_id') != before.get('conversation_id') or check.get('media',{}).get('context') != input_context:
            raise BackgroundError('REQUEST_CONTEXT_CHANGED', '准备期间对话或输入区发生变化，未发送。', request_id=request_id)
        self.store.update(request_id, draft_text=check.get('media',{}).get('draft_text',effective_prompt),
                          draft_visible_text=check.get('media',{}).get('visible_text'),
                          draft_serialized_text=check.get('media',{}).get('serialized_text'),
                          effective_prompt=effective_prompt, prompt_prefix_applied=True)
        self.store.mark_attempt(request_id)
        self.driver.sent = True  # From this point an interrupted call is never retried.
        try:
            result = self.driver.page('video_send_once', **arguments, proof=check['proof'])
            if not result.get('clicked'):
                raise BackgroundError('VIDEO_SEND_UNCONFIRMED', '未确认发送按钮是否已点击。')
            if result.get('conversation_id') != before.get('conversation_id'):
                raise BackgroundError('REQUEST_CONTEXT_CHANGED', '发送回执的对话与原请求不一致，停止并查询原请求。')
            self.store.update(request_id, state='submitted', send_clicked=True)
        except Exception as error:
            self.store.update(request_id, state='submission_unknown', send_error=str(error))
            return {'ok':False, 'code':'VIDEO_SUBMISSION_UNCERTAIN', 'request_id':request_id,
                    'submission_attempted':True, 'status':'submission_unknown', 'no_resend':True,
                    'message':'发送结果未确认；请用原request_id查询，不要换ID重发。'}
        deadline = time.monotonic()+wait_seconds
        try:
            while True:
                status = self.reader.query(request_id=request_id)
                record = self.store.get(request_id)
                if record.get('user_message_id') or status.get('message_id') or status.get('task_id') or time.monotonic() >= deadline:
                    return {**status, 'request_id':request_id, 'user_message_id':record.get('user_message_id'),
                            'submission_attempted':True, 'send_clicked':True, 'no_resend':True}
                time.sleep(.5)
        except Exception as error:
            return {'ok':False, 'code':'VIDEO_STATUS_UNAVAILABLE', 'request_id':request_id,
                    'submission_attempted':True, 'send_clicked':True, 'status':'submission_unknown',
                    'no_resend':True, 'message':'已点击发送，但暂时无法读取原任务；请按request_id查询。',
                    'reason':str(error)}

    def status(self, request_id=None, conversation_id=None, message_id=None, task_id=None):
        if not any((request_id,message_id,task_id)):
            raise BackgroundError('INVALID_TASK_TARGET', '请提供原request_id、message_id或task_id查询具体任务。')
        return self.reader.query(request_id, conversation_id, message_id, task_id)

    def confirm(self, request_id):
        record = self.store.get(request_id)
        if record.get('confirmation_attempted'):
            return {**self._reused(request_id), 'reused_confirmation':True, 'confirmation_attempted':True}
        status = self.reader.query(request_id=request_id)
        if status.get('status') != 'confirmation_required' or not record.get('submission_attempted'):
            raise BackgroundError('NOT_AWAITING_CONFIRMATION', '原请求当前不是待确认状态，未发送确认。', status=status)
        body = status.get('message_text','')
        model = re.search(r'(?:^|\n)\s*模型\s*[：:]\s*([^\n]+)',body)
        duration = re.search(r'(?:^|\n)\s*时长\s*[：:]\s*(\d+)\s*(?:秒|s)',body)
        ratio = re.search(r'(?:^|\n)\s*比例\s*[：:]\s*(自动|\d+:\d+)',body)
        payload = record['payload']
        if not model or not duration or not ratio or ''.join(model[1].split()) != ''.join(payload['model'].split()) or int(duration[1]) != payload['duration'] or ('auto' if ratio[1]=='自动' else ratio[1]) != payload['ratio']:
            raise BackgroundError('CONFIRMATION_PARAMETER_MISMATCH', '豆包确认页的模型、比例或秒数与原请求不一致，未确认。', request_id=request_id)
        record = self.store.get(request_id)
        message_id = record.get('confirmation_message_id') or status.get('message_id')
        conversation_id = record.get('conversation_id')
        if not record.get('user_message_id') or not message_id or not conversation_id:
            raise BackgroundError('CONFIRMATION_UNTRACKED', '缺少原始真实消息标识，不能确认。')
        prompt = (f"确认。按上述已确认的原始提示词和参考图片，使用{payload['model']}生成"
                  f"{payload['duration']}秒、{'自动比例' if payload['ratio']=='auto' else payload['ratio']+'画面比例'}视频。现在开始生成视频。")
        self.driver.page('focus_editor')
        self.driver.client.call('Input.insertText', {'text':prompt})
        arguments = {'prompt':prompt,'conversation_id':conversation_id,'confirmation_message_id':message_id}
        check = self.driver.page('video_confirmation_preflight', **arguments)
        if not check.get('verified') or not isinstance(check.get('proof'), dict) or not check['proof']:
            raise BackgroundError('CONFIRMATION_UNVERIFIED', '未确认原任务、参数和确认草稿，未发送。')
        before = self.driver.page('snapshot')
        self.store.mark_confirmation_attempt(request_id,prompt,[m['id'] for m in before['messages']])
        self.driver.sent = True
        try:
            result = self.driver.page('video_confirm_once', **arguments, proof=check['proof'])
            if not result.get('clicked'):
                raise BackgroundError('CONFIRMATION_UNVERIFIED', '未读到确认发送回执。')
            if result.get('conversation_id') != conversation_id:
                raise BackgroundError('REQUEST_CONTEXT_CHANGED', '确认回执不属于原对话，停止并查询原请求。')
            self.store.update(request_id, confirmation_clicked=True)
        except Exception as error:
            self.store.update(request_id, state='submission_unknown',confirmation_error=str(error))
            return {'ok':False,'request_id':request_id,'code':'CONFIRMATION_UNCERTAIN','status':'submission_unknown',
                    'no_resend':True,'message':'确认发送结果未知，请继续查询原request_id，禁止重复确认。'}
        try:
            return {**self.reader.query(request_id=request_id), 'request_id':request_id,
                    'confirmation_attempted':True,'confirmation_clicked':True,'no_resend':True}
        except Exception as error:
            return {'ok':False,'code':'VIDEO_STATUS_UNAVAILABLE','request_id':request_id,
                    'confirmation_attempted':True,'confirmation_clicked':True,'no_resend':True,
                    'status':'submission_unknown','reason':str(error),
                    'message':'确认已点击，但暂时无法读取原任务；请只查询原request_id。'}

    def open_detail(self, request_id=None, conversation_id=None, message_id=None, task_id=None, timeout=30):
        from .detail import open_detail
        if type(timeout) is not int or not 5 <= timeout <= 60:
            raise BackgroundError('INVALID_TIMEOUT','详情 timeout 必须为 5–60 秒。')
        status = self.status(request_id, conversation_id, message_id, task_id)
        return open_detail(self.driver, status, timeout)

    def download(self, directory, request_id=None, conversation_id=None, message_id=None, task_id=None,
                 filename=None, video_index=None, timeout=180, resource_kind='unwatermarked', open_detail_if_needed=True):
        from urllib.parse import urlparse, parse_qs
        from .detail import open_detail, _evaluate
        if type(timeout) is not int or not 10 <= timeout <= 240:
            raise BackgroundError('INVALID_TIMEOUT', '下载timeout必须为10–240秒。')
        if resource_kind not in {'unwatermarked','preview'} or type(open_detail_if_needed) is not bool:
            raise BackgroundError('INVALID_RESOURCE_KIND', '资源类型或详情选项无效。')
        if video_index is not None and (type(video_index) is not int or video_index < 0):
            raise BackgroundError('VIDEO_RESOURCE_UNAVAILABLE','资源索引无效。')
        status = self.status(request_id, conversation_id, message_id, task_id)
        if status.get('status') != 'completed':
            return {**status, 'ok':False, 'code':'VIDEO_NOT_COMPLETED', 'downloaded':False}
        videos = status.get('videos') or []
        def lr(v): return parse_qs(urlparse(v.get('url','')).query).get('lr',[None])[0]
        if resource_kind == 'unwatermarked':
            if open_detail_if_needed:
                try:
                    detail = open_detail(self.driver, status, min(timeout,30))
                except BackgroundError as error:
                    if error.code == 'DETAIL_OPEN_TIMEOUT':
                        raise BackgroundError('FORMAL_RESOURCE_UNAVAILABLE','正式资源不可用。') from error
                    raise
            else:
                self.driver.page('check_idle')
                state = _evaluate(self.driver, 'inspect', status)
                if len(state['matches']) != 1:
                    raise BackgroundError('FORMAL_RESOURCE_UNAVAILABLE','正式资源不可用。')
                detail = {'resource':{**state['matches'][0],'source':'rendered_video_player'}}
            video = detail['resource']
            if lr(video) != 'unwatermarked':
                raise BackgroundError('FORMAL_RESOURCE_UNAVAILABLE','正式资源不可用。')
            refreshed = self.status(None, status['conversation_id'], status['message_id'], status['task_id'])
            matching = [i for i,v in enumerate(refreshed.get('videos') or []) if v.get('url') == video['url']]
            selected_index = matching[0] if len(matching)==1 else None
            if video_index is not None and (not matching or video_index != selected_index):
                raise BackgroundError('VIDEO_RESOURCE_UNAVAILABLE','指定索引与正式资源不一致。')
            verified = True
        else:
            eligible=[(i,v) for i,v in enumerate(videos) if v.get('source')=='client_card_metadata' and v.get('url') and lr(v) != 'unwatermarked' and v.get('task_id')==status.get('task_id') and v.get('message_id')==status.get('message_id')]
            if video_index is not None: eligible=[pair for pair in eligible if pair[0]==video_index]
            if not eligible: raise BackgroundError('VIDEO_RESOURCE_UNAVAILABLE','预览资源不可用。')
            selected_index,video=eligible[0]; verified=True
        expected = {k:video[k] for k in ('duration','width','height','expected_bytes') if video.get(k)}
        if request_id: expected['duration'] = self.store.get(request_id)['payload']['duration']
        source_url=video['url']
        result=download_video(source_url, directory, filename=filename, expected_metadata=expected, timeout=timeout)
        result.update(request_id=request_id, conversation_id=status.get('conversation_id'), message_id=status.get('message_id'),
                      task_id=status.get('task_id'), source_url=source_url, url_lr=lr(video),
                      resource_source=video['source'], selected_video_index=selected_index, identity_verified=verified)
        if request_id: self.store.update(request_id,last_download=result)
        return result
