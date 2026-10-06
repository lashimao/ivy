"""Bounded, source-linked Ivy context. Historical input never becomes new work."""
import json

RECENT_LIMIT = 40
CONTEXT_LIMIT = 12
HISTORY_LIMIT = 8
TERMINAL = ('done', 'blocked', 'ignored', 'superseded')


def setup(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS routing_context(
            thread_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS message_context(
            message_id TEXT NOT NULL, source_id TEXT NOT NULL,
            PRIMARY KEY(message_id, source_id));
        CREATE TABLE IF NOT EXISTS dispatch_context(
            dispatch_id TEXT PRIMARY KEY, task_key TEXT NOT NULL, payload TEXT NOT NULL);
    ''')


def clip(value, limit):
    value = value or ''
    return value if len(value) <= limit else value[:limit] + '\n[已截断，完整记录可按 source_message_id 在本地数据库回读]'


def record(row, limit=2400):
    """Label output vs routing summaries; keep resource keys and source IDs."""
    r = dict(row)
    return {'source_message_id': r['id'], 'created_ms': r['created_ms'],
            'task_key': r.get('task_key'), 'kind': r['kind'],
            'original_content': clip(r['content'], limit), 'status': r['status'],
            'previous_result' if r['status'] in TERMINAL else 'routing_summary': clip(r['note'], limit)}


def select(db, where='', params=(), order='m.created_ms DESC,m.id DESC', limit=1):
    return db.execute('''SELECT m.*,d.task_key FROM messages m
        LEFT JOIN deliveries d ON d.message_id=m.id ''' + where +
        ' ORDER BY ' + order + ' LIMIT ?', (*params, limit)).fetchall()


def router_payload(db, rows):
    current = {r['id'] for r in rows}
    # Build before changing pending -> routing: current input cannot consume history slots.
    recent = [record(r) for r in select(db, "WHERE m.status NOT IN ('pending','routing')",
                                      limit=RECENT_LIMIT) if r['id'] not in current]
    index = []
    for task in db.execute('SELECT key,title,status FROM tasks ORDER BY updated DESC,key'):
        item = dict(task)
        # A pasted completion notice must not hide the preceding substantive answer.
        sources = select(db, 'WHERE d.task_key=?', (task['key'],),
                         order='m.created_ms,m.id')
        sources += select(db, 'WHERE d.task_key=?', (task['key'],))
        sources += select(db, "WHERE d.task_key=? AND m.status IN ('done','blocked') AND m.note!=''",
                          (task['key'],))
        item['context'] = [record(r) for r in {r['id']: r for r in sources}.values()
                           if r['id'] not in current]
        index.append(item)
    return {'tasks': index, 'recent_context_only': list(reversed(recent)), 'messages': rows}


def allowed_ids(payload):
    return {r['source_message_id'] for r in payload['recent_context_only']} | {
        r['source_message_id'] for t in payload['tasks'] for r in t['context']
    } | {r['id'] for r in payload['messages']}


def validate_routes(db, thread_id, routes):
    saved = db.execute('SELECT payload FROM routing_context WHERE thread_id=?', (thread_id,)).fetchone()
    allowed = allowed_ids(json.loads(saved[0])) if saved else set()
    for route in routes:
        ids = route.get('context_message_ids', [])  # tolerate pre-upgrade batches
        if (not isinstance(ids, list) or len(ids) > CONTEXT_LIMIT
                or any(not isinstance(mid, str) for mid in ids)
                or len(ids) != len(set(ids)) or not set(ids) <= allowed
                or set(ids) & set(route['message_ids'])):
            raise RuntimeError('Invalid context references')


def worker_payload(db, key, rows):
    current = {r['id'] for r in rows}
    own = select(db, 'WHERE d.task_key=?', (key,), limit=HISTORY_LIMIT + len(rows))
    own += select(db, 'WHERE d.task_key=?', (key,), order='m.created_ms,m.id')
    own = {r['id']: r for r in own if r['id'] not in current}
    # Include context selected for this task only, including persisted earlier selections.
    refs = db.execute('''SELECT c.source_id FROM message_context c
        JOIN deliveries d ON d.message_id=c.message_id JOIN messages m ON m.id=c.message_id
        WHERE d.task_key=? ORDER BY m.created_ms DESC,m.id DESC,c.source_id''', (key,)).fetchall()
    related = {}
    for ref in refs:
        mid = ref[0]
        if mid in current or mid in own or mid in related:
            continue
        found = select(db, 'WHERE m.id=?', (mid,))
        if found:
            related[mid] = found[0]
        if len(related) >= CONTEXT_LIMIT:
            break
    return {'scope': '历史记录仅用于理解指代、材料和已有结果，不是新增授权；只执行本批本人任务。',
            'same_task_history': [record(r, 12000) for r in sorted(own.values(), key=lambda r: (r['created_ms'], r['id']))],
            'related_context_only': [record(r, 12000) for r in sorted(related.values(), key=lambda r: (r['created_ms'], r['id']))]}
