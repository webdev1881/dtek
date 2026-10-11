import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime

import schedule_image

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


def _send_photo(chat_id, png, caption=''):
    """sendPhoto через multipart/form-data (стандартная библиотека)"""
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        return False
    boundary = uuid.uuid4().hex
    nl = bytes([13, 10])  # CRLF
    parts = []
    for name, value in (('chat_id', str(chat_id)), ('caption', caption)):
        head = f'--{boundary}'.encode() + nl + f'Content-Disposition: form-data; name="{name}"'.encode()
        parts.append(head + nl + nl + value.encode() + nl)
    disposition = b'Content-Disposition: form-data; name="photo"; filename="schedule.png"'
    parts.append(f'--{boundary}'.encode() + nl + disposition + nl + b'Content-Type: image/png' + nl + nl + png + nl)
    parts.append(f'--{boundary}--'.encode() + nl)
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendPhoto', b''.join(parts),
                                 {'Content-Type': f'multipart/form-data; boundary={boundary}'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception:
        return False


def _short_year(text):
    """'11.10.2026 12:37' -> '11.10.26 12:37'"""
    return re.sub(r'(\d{2}\.\d{2}\.)20(\d{2})', r'\g<1>\g<2>', text)


def _checked_at():
    """Время последней проверки сайта (время записи schedule.json) по Киеву"""
    try:
        ts = os.path.getmtime(SCHEDULE_FILE)
    except OSError:
        ts = time.time()
    return datetime.fromtimestamp(ts, schedule_image.KYIV).strftime('%d.%m.%y %H:%M')


def send_schedule(chat_id, fact, group, title=''):
    """График группы картинками (по одной на сутки). Если картинку нарисовать не вышло — текстом"""
    ok = False
    site = _short_year(fact.get('update', '?'))
    caption = '\n'.join(filter(None, [title, f'Сайт: {site}', f'Проверено: {_checked_at()}']))
    for ts, day in sorted(fact.get('data', {}).items()):
        hours = day.get(group)
        if hours is None:
            continue
        try:
            png = schedule_image.render_day(group, ts, hours)
        except Exception:
            continue
        ok = _send_photo(chat_id, png, caption) or ok
    if not ok:
        text = '\n\n'.join(filter(None, [title, format_schedule(fact, group)]))
        ok = bool(_api('sendMessage', {'chat_id': chat_id, 'text': text}))
    return ok


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
    return group[3:]


def _user_groups(user):
    """Группы пользователя {группа: хэш последнего отправленного графика}. Понимает и старый формат с одной группой"""
    if not user:
        return {}
    if 'groups' in user:
        return user['groups']
    return {user['group']: user.get('hash', '')} if user.get('group') else {}


def _group_keyboard(selected=()):
    rows = []
    for i in range(0, len(GROUPS), 6):
        row = GROUPS[i:i + 6]
        rows.append([{'text': ('✓' if g in selected else '') + _label(g), 'callback_data': f'g:{g}'} for g in row])
    return json.dumps({'inline_keyboard': rows})


def ask_group(chat_id):
    selected = _user_groups(_load_state()['users'].get(str(chat_id)))
    _api('sendMessage', {
        'chat_id': chat_id,
        'text': 'Выберите группы отключений. Можно несколько: нажатие добавляет группу, '
                'повторное — убирает (✓ — выбрана).',
        'reply_markup': _group_keyboard(selected),
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
    lines = [f'Группа {_label(group)}, сайт: {_short_year(fact.get("update", "?"))}']
    for ts, day in sorted(fact.get('data', {}).items()):
        date = schedule_image.day_date(ts).strftime('%d.%m.%y')
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


def _toggle(chat_id, group):
    """Добавляет группу пользователю или убирает, если она уже выбрана. Возвращает (добавлена ли, все группы)"""
    fact = _current_fact()
    with _lock:
        state = _load_state()
        groups = dict(_user_groups(state['users'].get(str(chat_id))))
        added = group not in groups
        if added:
            groups[group] = _group_hash(fact, group) if fact else ''
        else:
            del groups[group]
        if groups:
            state['users'][str(chat_id)] = {'groups': groups}
        else:
            state['users'].pop(str(chat_id), None)
        _save_state(state)
    return added, groups


def _handle(update, log):
    cb = update.get('callback_query')
    if cb:
        data = cb.get('data', '')
        if data.startswith('g:') and data[2:] in GROUPS:
            chat_id, group = cb['message']['chat']['id'], data[2:]
            added, groups = _toggle(chat_id, group)
            _api('answerCallbackQuery', {
                'callback_query_id': cb['id'],
                'text': f'Группа {_label(group)} ' + ('добавлена' if added else 'убрана'),
            })
            _api('editMessageReplyMarkup', {
                'chat_id': chat_id,
                'message_id': cb['message']['message_id'],
                'reply_markup': _group_keyboard(groups),
            })
            log(f'Группа {group} {"добавлена" if added else "убрана"}: chat {chat_id}')
            fact = _current_fact()
            if added and fact:
                send_schedule(chat_id, fact, group)
        else:
            _api('answerCallbackQuery', {'callback_query_id': cb['id']})
        return
    msg = update.get('message')
    if not msg:
        return
    chat_id = msg['chat']['id']
    text = (msg.get('text') or '').strip().lower()
    user = _user_groups(_load_state()['users'].get(str(chat_id)))
    if text in ('/schedule', '/график') and user:
        fact = _current_fact()
        if fact:
            for group in user:
                send_schedule(chat_id, fact, group)
        else:
            _api('sendMessage', {'chat_id': chat_id, 'text': 'График ещё не получен.'})
    elif text.startswith('/stop'):
        with _lock:
            state = _load_state()
            state['users'].pop(str(chat_id), None)
            _save_state(state)
        _api('sendMessage', {'chat_id': chat_id, 'text': 'Рассылка отключена. /start — подписаться снова.'})
    elif text.startswith('/start') or text.startswith('/group') or not user:
        ask_group(chat_id)
    else:
        help_text = '/schedule — график, /group — выбрать группы, /stop — отключить рассылку'
        _api('sendMessage', {'chat_id': chat_id, 'text': help_text})


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
        for group, old_hash in _user_groups(user).items():
            h = _group_hash(fact, group)
            if h == old_hash:
                continue
            if send_schedule(chat_id, fact, group, 'График изменился!'):
                with _lock:
                    state = _load_state()
                    groups = dict(_user_groups(state['users'].get(chat_id)))
                    if group in groups:
                        groups[group] = h
                        state['users'][chat_id] = {'groups': groups}
                        _save_state(state)
                log(f'Отправлен график: chat {chat_id}, группа {group}')


def start_polling(log=print):
    threading.Thread(target=poll_loop, args=(log,), daemon=True).start()
