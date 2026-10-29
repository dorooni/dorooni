// 가계부 기기 간 동기화 API.
// /api/sync 만 처리하고, 나머지 요청은 정적 자산으로 넘긴다.
// 데이터는 KV(SYNC)에 "u:<해시>" 키로 저장한다. 해시는 브라우저가 동기화 비밀번호로 만든
// SHA-256 값이라 비밀번호 원문은 서버로 오지 않는다.

const MAX_BODY = 1024 * 1024;
const MAX_ITEMS = 20000;
const TOMBSTONE_TTL = 60 * 24 * 60 * 60 * 1000; // 삭제 표시는 60일 뒤 정리

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== '/api/sync') {
      if (url.pathname.startsWith('/api/')) return json({ error: 'not_found' }, 404);
      return env.ASSETS.fetch(request);
    }

    const key = request.headers.get('X-Sync-Key') || '';
    if (!/^[0-9a-f]{64}$/.test(key)) return json({ error: 'bad_key' }, 400);
    const kvKey = 'u:' + key;

    if (request.method === 'GET') {
      return json(await readDoc(env, kvKey));
    }

    if (request.method === 'POST') {
      const len = Number(request.headers.get('Content-Length') || 0);
      if (len > MAX_BODY) return json({ error: 'too_large' }, 413);
      const text = await request.text();
      if (text.length > MAX_BODY) return json({ error: 'too_large' }, 413);

      let incoming;
      try { incoming = JSON.parse(text); } catch { return json({ error: 'bad_json' }, 400); }
      if (!incoming || typeof incoming !== 'object') return json({ error: 'bad_json' }, 400);

      const entries = cleanList(incoming.entries, cleanEntry);
      const todos = cleanList(incoming.todos, cleanTodo);
      if (!entries || !todos) return json({ error: 'bad_data' }, 400);

      const stored = await readDoc(env, kvKey);
      const now = Date.now();
      const doc = {
        entries: prune(merge(stored.entries, entries), now),
        todos: prune(merge(stored.todos, todos), now),
        updatedAt: now,
      };
      // 바뀐 게 없으면 KV 쓰기를 아낀다 (무료 한도: 하루 1,000회)
      if (JSON.stringify(doc.entries) !== JSON.stringify(stored.entries) ||
          JSON.stringify(doc.todos) !== JSON.stringify(stored.todos)) {
        await env.SYNC.put(kvKey, JSON.stringify(doc));
      } else {
        doc.updatedAt = stored.updatedAt;
      }
      return json(doc);
    }

    return json({ error: 'method_not_allowed' }, 405);
  },
};

async function readDoc(env, kvKey) {
  const raw = await env.SYNC.get(kvKey);
  if (!raw) return { entries: [], todos: [], updatedAt: 0 };
  try {
    const d = JSON.parse(raw);
    return { entries: d.entries || [], todos: d.todos || [], updatedAt: d.updatedAt || 0 };
  } catch {
    return { entries: [], todos: [], updatedAt: 0 };
  }
}

// id 기준으로 합치고, 같은 id면 updatedAt이 더 큰 쪽을 남긴다.
function merge(a, b) {
  const map = new Map();
  for (const x of a) map.set(x.id, x);
  for (const x of b) {
    const cur = map.get(x.id);
    if (!cur || (x.updatedAt || 0) >= (cur.updatedAt || 0)) map.set(x.id, x);
  }
  return [...map.values()].sort((p, q) => (p.id < q.id ? -1 : p.id > q.id ? 1 : 0));
}

function prune(list, now) {
  return list.filter(x => !(x.deleted && now - (x.updatedAt || 0) > TOMBSTONE_TTL));
}

function cleanList(list, fn) {
  if (!Array.isArray(list) || list.length > MAX_ITEMS) return null;
  const out = [];
  for (const x of list) {
    const c = fn(x);
    if (c) out.push(c);
  }
  return out;
}

const str = (v, max) => (typeof v === 'string' ? v.slice(0, max) : '');
const num = v => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

function cleanEntry(x) {
  if (!x || typeof x !== 'object' || typeof x.id !== 'string' || !x.id || x.id.length > 64) return null;
  return {
    id: x.id,
    date: /^\d{4}-\d{2}-\d{2}$/.test(x.date) ? x.date : '',
    type: x.type === 'income' ? 'income' : 'expense',
    amount: Math.max(0, Math.round(num(x.amount))),
    category: str(x.category, 20),
    memo: str(x.memo, 200),
    updatedAt: num(x.updatedAt),
    deleted: x.deleted === true,
  };
}

function cleanTodo(x) {
  if (!x || typeof x !== 'object' || typeof x.id !== 'string' || !x.id || x.id.length > 64) return null;
  return {
    id: x.id,
    text: str(x.text, 500),
    done: x.done === true,
    createdAt: num(x.createdAt),
    updatedAt: num(x.updatedAt),
    deleted: x.deleted === true,
  };
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}
