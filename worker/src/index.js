// Telegram-бот графика отключений ДТЕК на Cloudflare Worker.
// Скрапер (GitHub Actions) загружает готовые PNG и метаданные через /upload и /commit,
// Worker отвечает на команды Telegram через webhook и рассылает изменения подписчикам.

const GROUPS = Array.from({ length: 60 }, (_, i) => `GPV${i + 1}.1`);
const label = (g) => g.slice(3);

// ---------- Telegram ----------

async function tg(env, method, params) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(params),
  });
  return r.json();
}

async function sendPhoto(env, chatId, pngB64, caption) {
  const bytes = Uint8Array.from(atob(pngB64), (c) => c.charCodeAt(0));
  const form = new FormData();
  form.append('chat_id', String(chatId));
  form.append('caption', caption);
  form.append('photo', new Blob([bytes], { type: 'image/png' }), 'schedule.png');
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendPhoto`, { method: 'POST', body: form });
  return r.ok;
}

function keyboard(selected) {
  const rows = [];
  for (let i = 0; i < GROUPS.length; i += 6) {
    rows.push(
      GROUPS.slice(i, i + 6).map((g) => ({
        text: (selected[g] !== undefined ? '✓' : '') + label(g),
        callback_data: `g:${g}`,
      })),
    );
  }
  return { inline_keyboard: rows };
}

// ---------- Хранилище ----------

const getJSON = (env, key) => env.DTEK.get(key, 'json');
const putJSON = (env, key, value) => env.DTEK.put(key, JSON.stringify(value));

async function getGroups(env, chatId) {
  const sub = await getJSON(env, `sub:${chatId}`);
  return (sub && sub.groups) || {};
}

async function setGroups(env, chatId, groups) {
  if (Object.keys(groups).length) await putJSON(env, `sub:${chatId}`, { groups });
  else await env.DTEK.delete(`sub:${chatId}`);
}

// ---------- Отправка графика ----------

async function sendSchedule(env, chatId, group, title = '') {
  const meta = (await getJSON(env, 'meta')) || {};
  const days = await getJSON(env, `img:${group}`);
  if (!days || !days.length) {
    await tg(env, 'sendMessage', { chat_id: chatId, text: `Графік групи ${label(group)} ще не отримано.` });
    return false;
  }
  const caption = [title, `Сайт: ${meta.update ?? '?'}`, `Перевірено: ${meta.checked ?? '?'}`].filter(Boolean).join('\n');
  let ok = false;
  for (const day of days) ok = (await sendPhoto(env, chatId, day.png, caption)) || ok;
  return ok;
}

// ---------- Webhook ----------

async function onCallback(env, cb) {
  const data = cb.data || '';
  if (!data.startsWith('g:') || !GROUPS.includes(data.slice(2))) {
    await tg(env, 'answerCallbackQuery', { callback_query_id: cb.id });
    return;
  }
  const group = data.slice(2);
  const chatId = cb.message.chat.id;
  const groups = await getGroups(env, chatId);
  const meta = (await getJSON(env, 'meta')) || {};
  const added = groups[group] === undefined;
  if (added) groups[group] = (meta.hashes && meta.hashes[group]) || '';
  else delete groups[group];
  await setGroups(env, chatId, groups);
  await tg(env, 'answerCallbackQuery', {
    callback_query_id: cb.id,
    text: `Група ${label(group)} ${added ? 'додана' : 'прибрана'}`,
  });
  await tg(env, 'editMessageReplyMarkup', {
    chat_id: chatId,
    message_id: cb.message.message_id,
    reply_markup: keyboard(groups),
  });
  if (added) await sendSchedule(env, chatId, group);
}

async function askGroups(env, chatId) {
  const groups = await getGroups(env, chatId);
  await tg(env, 'sendMessage', {
    chat_id: chatId,
    text: 'Оберіть групи відключень. Можна кілька: натискання додає групу, повторне — прибирає (✓ — обрана).',
    reply_markup: keyboard(groups),
  });
}

async function onMessage(env, msg) {
  const chatId = msg.chat.id;
  const text = (msg.text || '').trim().toLowerCase().split('@')[0];
  const groups = await getGroups(env, chatId);
  const has = Object.keys(groups).length > 0;
  if ((text === '/schedule' || text === '/графік' || text === '/график') && has) {
    for (const g of Object.keys(groups)) await sendSchedule(env, chatId, g);
  } else if (text.startsWith('/stop')) {
    await setGroups(env, chatId, {});
    await tg(env, 'sendMessage', { chat_id: chatId, text: 'Розсилку вимкнено. /start — підписатися знову.' });
  } else if (text.startsWith('/start') || text.startsWith('/group') || !has) {
    await askGroups(env, chatId);
  } else {
    await tg(env, 'sendMessage', {
      chat_id: chatId,
      text: '/schedule — графік, /group — обрати групи, /stop — вимкнути розсилку',
    });
  }
}

async function onWebhook(request, env) {
  if (request.headers.get('x-telegram-bot-api-secret-token') !== env.WEBHOOK_SECRET) {
    return new Response('forbidden', { status: 403 });
  }
  const update = await request.json();
  try {
    if (update.callback_query) await onCallback(env, update.callback_query);
    else if (update.message) await onMessage(env, update.message);
  } catch (e) {
    console.log('webhook error', String(e));
  }
  return new Response('ok');
}

// ---------- API для скрапера ----------

const authorized = (request, env) => request.headers.get('authorization') === `Bearer ${env.UPLOAD_TOKEN}`;

// Рассылка подписчикам, у которых изменился график их группы
async function notifyAll(env, meta) {
  let cursor;
  do {
    const page = await env.DTEK.list({ prefix: 'sub:', cursor });
    for (const { name } of page.keys) {
      const chatId = name.slice(4);
      const groups = await getGroups(env, chatId);
      let changed = false;
      for (const g of Object.keys(groups)) {
        const hash = (meta.hashes && meta.hashes[g]) || '';
        if (hash === groups[g]) continue;
        if (await sendSchedule(env, chatId, g, 'Графік змінився!')) {
          groups[g] = hash;
          changed = true;
        }
      }
      if (changed) await setGroups(env, chatId, groups);
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
}

async function onApi(request, env, ctx, path) {
  if (!authorized(request, env)) return new Response('forbidden', { status: 403 });
  if (path === '/state') {
    const meta = (await getJSON(env, 'meta')) || {};
    return Response.json({ hash: meta.hash ?? null, checked: meta.checked ?? null });
  }
  const body = await request.json();
  if (path === '/upload') {
    // body.groups = { "GPV1.1": [{ts, png}], ... }
    for (const [g, days] of Object.entries(body.groups || {})) await putJSON(env, `img:${g}`, days);
    return Response.json({ ok: true, groups: Object.keys(body.groups || {}).length });
  }
  if (path === '/commit') {
    // body.meta = { update, checked, hash, hashes: {group: hash} }
    await putJSON(env, 'meta', body.meta);
    ctx.waitUntil(notifyAll(env, body.meta));
    return Response.json({ ok: true });
  }
  if (path === '/touch') {
    const meta = (await getJSON(env, 'meta')) || {};
    meta.checked = body.checked;
    await putJSON(env, 'meta', meta);
    return Response.json({ ok: true });
  }
  return new Response('not found', { status: 404 });
}

export default {
  async fetch(request, env, ctx) {
    const path = new URL(request.url).pathname;
    if (request.method === 'POST' && path === '/webhook') return onWebhook(request, env);
    if (request.method === 'GET' && path === '/state') return onApi(request, env, ctx, path);
    if (request.method === 'POST') return onApi(request, env, ctx, path);
    return new Response('dtek bot');
  },
};
