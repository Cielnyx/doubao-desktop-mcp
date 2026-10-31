"""Open an identified completed video using only background CDP events."""
import json
import time
from pathlib import Path
from .background import BackgroundError

SCRIPT = Path(__file__).with_name('detail.js').read_text(encoding='utf-8')

def _evaluate(driver, mode, status):
    args = dict(mode=mode, message_id=status['message_id'], conversation_id=status['conversation_id'], task_id=status['task_id'])
    result = driver.client.evaluate('(async()=>{' + SCRIPT + '\nreturn await detail(' + json.dumps(args) + ');})()', timeout=25)
    if not result.get('ok'):
        raise BackgroundError(result.get('code','DETAIL_BLOCKED'), result.get('message','详情不可用。'))
    return result

def open_detail(driver, status, timeout=30):
    if type(timeout) is not int or not 5 <= timeout <= 60:
        raise BackgroundError('INVALID_TIMEOUT','详情 timeout 必须为 5–60 秒。')
    if status.get('status') != 'completed' or not all(status.get(k) for k in ('conversation_id','message_id','task_id')):
        raise BackgroundError('RESOURCE_IDENTITY_UNVERIFIED','缺少已完成任务的真实身份。')
    driver.page('check_idle')
    state = _evaluate(driver,'locate',status)
    already = state.get('already_open',False)
    if not already:
        driver.page('check_idle')
        state = _evaluate(driver,'locate',status)
        if state.get('already_open'):
            already = True
        else:
            point=state['point']
        if not already:
            driver.client.call('Input.dispatchMouseEvent', {'type':'mouseMoved',**point})
            driver.page('check_idle')
            checked = _evaluate(driver,'locate',status)
            if checked.get('already_open'):
                already = True
            elif checked.get('point') != point:
                raise BackgroundError('DETAIL_BLOCKED','悬停后封面位置变化，未点击。')
            else:
                for kind in ('mousePressed','mouseReleased'):
                    driver.client.call('Input.dispatchMouseEvent', {'type':kind,**point,'button':'left','clickCount':1})
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            state=_evaluate(driver,'inspect',status)
            if len(state['matches'])==1: break
            time.sleep(.25)
        else: raise BackgroundError('DETAIL_OPEN_TIMEOUT','未能验证目标消息与详情播放器。')
    if len(state['matches'])!=1:
        raise BackgroundError('RESOURCE_IDENTITY_UNVERIFIED','无法唯一验证目标播放器。')
    resource={**state['matches'][0], 'source':'rendered_video_player','url_lr':'unwatermarked'}
    return {'ok':True,'background':True,**{k:status[k] for k in ('conversation_id','message_id','task_id')},
            'detail_opened':True,'already_open':already,'identity_verified':True,
            'identity_evidence':{'unique_task_card':True,'message_player_matches_panel':True},'resource':resource}
