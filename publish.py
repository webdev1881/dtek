import base64
import hashlib
import json
import os
import re
import urllib.request
from datetime import datetime

import schedule_image

BATCH = 10  # групп за один запрос /upload (лимит запросов Worker на бесплатном тарифе)


def _call(path, payload=None, timeout=60):
    """Запрос к Worker: GET без payload, иначе POST с JSON. Возвращает разобранный ответ"""
    base = os.environ['WORKER_URL'].rstrip('/')
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base + path, data, {
        'authorization': f'Bearer {os.environ["UPLOAD_TOKEN"]}',
        'content-type': 'application/json',
        'user-agent': 'dtek-publisher/1.0',  # без своего User-Agent Cloudflare отвечает 403
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _short_year(text):
    """'11.10.2026 12:37' -> '11.10.26 12:37'"""
    return re.sub(r'(\d{2}\.\d{2}\.)20(\d{2})', r'\g<1>\g<2>', text)


def _group_hash(fact, group):
    return json.dumps([d.get(group) for _, d in sorted(fact.get('data', {}).items())], sort_keys=True)


def publish(fact, log=print):
    """Отдаёт график боту на Cloudflare Worker. Если данные не менялись — только обновляет время проверки"""
    if not os.environ.get('WORKER_URL') or not os.environ.get('UPLOAD_TOKEN'):
        log('WORKER_URL/UPLOAD_TOKEN не заданы — публикация в бота пропущена', 'WARNING')
        return False
    checked = datetime.now(schedule_image.KYIV).strftime('%d.%m.%y %H:%M')
    data_hash = hashlib.sha256(json.dumps(fact.get('data', {}), sort_keys=True).encode()).hexdigest()
    try:
        if _call('/state').get('hash') == data_hash:
            _call('/touch', {'checked': checked})
            log(f'Данные не изменились, время проверки обновлено: {checked}')
            return True

        days = sorted(fact.get('data', {}).items())
        groups = sorted({g for _, day in days for g in day})
        images = {}
        for group in groups:
            images[group] = [
                {'ts': ts, 'png': base64.b64encode(schedule_image.render_day(group, ts, day[group])).decode()}
                for ts, day in days if group in day
            ]
        for i in range(0, len(groups), BATCH):
            chunk = {g: images[g] for g in groups[i:i + BATCH]}
            _call('/upload', {'groups': chunk})
        meta = {
            'update': _short_year(fact.get('update', '?')),
            'checked': checked,
            'hash': data_hash,
            'hashes': {g: _group_hash(fact, g) for g in groups},
        }
        _call('/commit', {'meta': meta})
        log(f'✓ График опубликован в боте: групп {len(groups)}, сайт: {meta["update"]}', 'SUCCESS')
        return True
    except Exception as e:
        log(f'Ошибка публикации в бота: {type(e).__name__}: {e}', 'ERROR')
        return False
