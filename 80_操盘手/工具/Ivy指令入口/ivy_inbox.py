#!/usr/bin/env python3
"""Ivy incremental inbox. Collection never executes messages or replies."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
ENV = Path(os.environ.get('IVY_ENV_FILE', str(ROOT.parents[2] / '.env')))
BASE = 'https://open.feishu.cn/open-apis'
_AUTH_CACHE = None


def api(path, data=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(BASE + path, headers=headers,
        data=None if data is None else json.dumps(data).encode())
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get('code') != 0:
        raise RuntimeError('Feishu API code=' + str(payload.get('code')))
    return payload


def load_config():
    env = {}
    for line in ENV.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            if key.strip() in ('FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'FEISHU_CHAT_ID'):
                env[key.strip()] = value.strip().strip('\"\'')
    return env


def connect():
    global _AUTH_CACHE
    if _AUTH_CACHE and _AUTH_CACHE[2] > time.time()+60:
        return _AUTH_CACHE[:2]
    env=load_config()
    result = api('/auth/v3/tenant_access_token/internal', {
        'app_id': env['FEISHU_APP_ID'], 'app_secret': env['FEISHU_APP_SECRET']
    })
    token=result['tenant_access_token']
    _AUTH_CACHE=(env['FEISHU_CHAT_ID'],token,time.time()+result.get('expire',3600))
    return env['FEISHU_CHAT_ID'], token


def page(chat, token, **params):
    query = {'container_id_type': 'chat', 'container_id': chat,
             'page_size': 50, **params}
    return api('/im/v1/messages?' + urllib.parse.urlencode(query), token=token)['data']


def database():
    os.umask(0o077)
    runtime = ROOT / 'runtime'
    runtime.mkdir(exist_ok=True, mode=0o700)
    db = sqlite3.connect(runtime / 'inbox.sqlite3', timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(
          id TEXT PRIMARY KEY, created_ms INTEGER, kind TEXT, content TEXT,
          status TEXT NOT NULL DEFAULT 'pending', note TEXT NOT NULL DEFAULT '',
          updated_ms INTEGER NOT NULL);
    ''')
    return db


def meta(db):
    return dict(db.execute('SELECT key,value FROM meta').fetchall())


def save_meta(db, key, value):
    db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, str(value)))


def initialize(db):
    if meta(db):
        raise RuntimeError('Already initialized; cursor was not reset')
    started = int(time.time() * 1000)
    chat, token = connect()
    data = page(chat, token, sort_type='ByCreateTimeDesc')
    senders = {m['sender']['id'] for m in data.get('items', [])
               if m.get('sender', {}).get('sender_type') == 'user'}
    # The configured Ivy conversation was explicitly authorized by its owner.
    # Pin its sole recent human sender; fail closed if ambiguous.
    if len(senders) != 1:
        raise RuntimeError('Cannot uniquely bind Ivy human sender')
    with db:
        save_meta(db, 'chat', chat)
        save_meta(db, 'sender', senders.pop())
        save_meta(db, 'started_ms', started)
        save_meta(db, 'cursor_s', started // 1000)
    return {'initialized': True, 'history_replayed': False}


def collect(db):
    cfg = meta(db)
    if not cfg:
        raise RuntimeError('Run init once first')
    chat, token = connect()
    if chat != cfg['chat']:
        raise RuntimeError('Ivy chat changed; refusing automatic rebinding')
    end = int(time.time())
    params = dict(sort_type='ByCreateTimeAsc',
                  start_time=max(0, int(cfg['cursor_s']) - 120), end_time=end)
    rows = []
    pages = set()
    while True:
        data = page(chat, token, **params)
        for msg in data.get('items', []):
            sender = msg.get('sender', {})
            created = int(msg['create_time'])
            if (sender.get('sender_type') != 'user' or sender.get('id') != cfg['sender']
                    or msg.get('deleted') or created < int(cfg['started_ms'])):
                continue
            rows.append((msg['message_id'], created, msg['msg_type'],
                         msg.get('body', {}).get('content', '{}'), int(time.time()*1000)))
        if not data.get('has_more'):
            break
        next_page = data.get('page_token')
        if not next_page or next_page in pages:
            raise RuntimeError('Invalid pagination; cursor unchanged')
        pages.add(next_page)
        params['page_token'] = next_page
    # Only advance the cursor after all pages and durable queue insertion succeed.
    with db:
        before = db.total_changes
        db.executemany('INSERT OR IGNORE INTO messages(id,created_ms,kind,content,updated_ms) VALUES(?,?,?,?,?)', rows)
        added = db.total_changes - before
        save_meta(db, 'cursor_s', end)
    return {'new_messages': added, 'queue': pending(db)}


def pending(db):
    return [dict(row) for row in db.execute(
        "SELECT * FROM messages WHERE status IN ('pending','working','blocked') ORDER BY created_ms,id")]


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('init', 'poll', 'pending', 'status'):
        sub.add_parser(command)
    mark = sub.add_parser('mark')
    mark.add_argument('message_id')
    mark.add_argument('status', choices=['working', 'blocked', 'done', 'ignored', 'superseded'])
    mark.add_argument('--note', required=True)
    args = parser.parse_args()
    db = database()
    if args.command == 'init':
        result = initialize(db)
    elif args.command == 'poll':
        result = collect(db)
    elif args.command == 'pending':
        result = {'queue': pending(db)}
    elif args.command == 'status':
        result = {'initialized': bool(meta(db)), 'counts': dict(db.execute(
            'SELECT status,count(*) FROM messages GROUP BY status').fetchall())}
    else:
        with db:
            changed = db.execute('UPDATE messages SET status=?,note=?,updated_ms=? WHERE id=?',
                (args.status, args.note, int(time.time()*1000), args.message_id)).rowcount
            if changed != 1:
                raise RuntimeError('Unknown message ID')
        result = {'updated': True}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not print request headers, credentials, or API response bodies.
        print(json.dumps({'error': type(exc).__name__, 'detail': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
