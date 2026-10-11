import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

STATE_FILE = 'bot_state.json'
SCHEDULE_FILE = 'schedule.json'
GROUPS = [f'GPV{i}.1' for i in range(1, 61)]

_lock = threading.Lock()


def _api(method, params=None, timeout=20):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        return None
    data = urllib.parse.urlencode(params or {}).encode()
    try:
        with urllib.request.urlopen(f'https://api.telegram.org/bot{token}/{method}', data, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def _load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {'offset': 0, 'users': {}}


def _save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _label(group):
    return group[3:].removesuffix('.1')


def _group_keyboard():
    rows = []
    for i in range(0, len(GROUPS), 6):
        rows.append([{'text': _label(g), 'callback_data': f'g:{g}'} for g in GROUPS[i:i + 6]])
    return json.dumps({'inline_keyboard': rows})


def ask_group(chat_id):
    _api('sendMessage', {
        'chat_id': chat_id,
        'text': 'Выберите вашу группу отключений:',
        'reply_markup': _group_keyboard(),
    })


def _intervals(hours, statuses):
    """Интервалы по часам 1..24 для заданных статусов. Статус -> (начало, конец) внутри часа в минутах"""
    segs = []
    for h in range(1, 25):
        part = statuses.get(hours.get(str(h), 'yes'))
        if part:
            start = (h - 1) * 60
            segs.append((start + part[0], start + part[1]))
    merged = []
    for s, e in segs:
        if merged and merged[-1][1] == s:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


# no — час целиком, first/second — первые/вторые 30 минут; m* — то же, но «возможно»
CERTAIN = {'no': (0, 60), 'first': (0, 30), 'second': (30, 60)}
MAYBE = {'maybe': (0, 60), 'mfirst': (0, 30), 'msecond': (30, 60)}


def _fmt(minutes):
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def format_schedule(fact, group):
    lines = [f'Группа {_label(group)}, обновлено на сайте: {fact.get("update", "?")}']
    for ts, day in sorted(fact.get('data', {}).items()):
        date = datetime.fromtimestamp(int(ts)).strftime('%d.%m.%Y')
        hours = day.get(group)
        if hours is None:
            continue
        outs = _intervals(hours, CERTAIN)
        maybe = _intervals(hours, MAYBE)
        parts = []
        if outs:
            parts.append('без света ' + ', '.join(f'{_fmt(s)}–{_fmt(e)}' for s, e in outs))
        if maybe:
            parts.append('возможно без света ' + ', '.join(f'{_fmt(s)}–{_fmt(e)}' for s, e in maybe))
        lines.append(f'{date}: ' + '; '.join(parts) if parts else f'{date}: отключений нет')
    return '\n'.join(lines)


def _group_hash(fact, group):
    return json.dumps([d.get(group) for _, d in sorted(fact.get('data', {}).items())], sort_keys=True)


def _current_fact():
    try:
        with open(SCHEDULE_FILE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _subscribe(chat_id, group):
    fact = _current_fact()
    with _lock:
        state = _load_state()
        state['users'][str(chat_id)] = {'group': group, 'hash': _group_hash(fact, group) if fact else ''}
        _save_state(state)
    text = f'Группа {_label(group)} сохранена. Пришлю график, когда он изменится.'
    if fact:
        text += '\n\n' + format_schedule(fact, group)
    _api('sendMessage', {'chat_id': chat_id, 'text': text})


def _handle(update, log):
    cb = update.get('callback_query')
    if cb:
        _api('answerCallbackQuery', {'callback_query_id': cb['id']})
        data = cb.get('data', '')
        if data.startswith('g:') and data[2:] in GROUPS:
            _subscribe(cb['message']['chat']['id'], data[2:])
            log(f'Подписка: chat {cb["message"]["chat"]["id"]} -> {data[2:]}')
        return
    msg = update.get('message')
    if not msg:
        return
    chat_id = msg['chat']['id']
    text = (msg.get('text') or '').strip().lower()
    user = _load_state()['users'].get(str(chat_id))
    if text in ('/schedule', '/график') and user:
        fact = _current_fact()
        _api('sendMessage', {'chat_id': chat_id, 'text': format_schedule(fact, user['group']) if fact else 'График ещё не получен.'})
    elif text.startswith('/stop'):
        with _lock:
            state = _load_state()
            state['users'].pop(str(chat_id), None)
            _save_state(state)
        _api('sendMessage', {'chat_id': chat_id, 'text': 'Рассылка отключена. /start — подписаться снова.'})
    elif text.startswith('/start') or text.startswith('/group') or not user:
        ask_group(chat_id)
    else:
        _api('sendMessage', {'chat_id': chat_id, 'text': '/schedule — график, /group — сменить группу, /stop — отключить рассылку'})


def poll_once(log=print, timeout=0):
    """Один запрос getUpdates: обрабатывает накопившиеся команды и выбор группы. Возвращает False при ошибке"""
    offset = _load_state().get('offset', 0)
    res = _api('getUpdates', {'offset': offset, 'timeout': timeout}, timeout=timeout + 10)
    if not res or not res.get('ok'):
        return False
    for upd in res['result']:
        try:
            _handle(upd, log)
        except Exception as e:
            log(f'Ошибка обработки апдейта бота: {e}')
        with _lock:
            state = _load_state()
            state['offset'] = upd['update_id'] + 1
            _save_state(state)
    return True


def poll_loop(log=print):
    """Long polling: принимаем команды и выбор группы. Запускать в отдельном потоке"""
    while True:
        if not poll_once(log, timeout=25):
            time.sleep(5)


def notify_all(fact, log=print):
    """Рассылка подписчикам, у которых изменился график их группы"""
    with _lock:
        state = _load_state()
        users = dict(state['users'])
    for chat_id, user in users.items():
        h = _group_hash(fact, user['group'])
        if h == user.get('hash'):
            continue
        if _api('sendMessage', {'chat_id': chat_id, 'text': 'График изменился!\n\n' + format_schedule(fact, user['group'])}):
            with _lock:
                state = _load_state()
                if chat_id in state['users']:
                    state['users'][chat_id]['hash'] = h
                    _save_state(state)
            log(f'Отправлен график: chat {chat_id}, группа {user["group"]}')


def start_polling(log=print):
    threading.Thread(target=poll_loop, args=(log,), daemon=True).start()
