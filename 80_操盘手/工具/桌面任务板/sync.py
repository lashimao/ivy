#!/usr/bin/env python3
"""Local, read-only task adapters. No model calls, network, or source DB writes."""
import argparse, datetime as dt, fcntl, json, os, re, sqlite3, time
from pathlib import Path
from project_model import project_rows, EMPTY

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = Path.home() / 'Library/Application Support/IvyTaskBoard'
IVY = ROOT / '80_操盘手/工具/Ivy指令入口/runtime/inbox.sqlite3'
CODEX = Path.home() / '.codex/state_5.sqlite'
CLAUDE = Path.home() / 'Library/Application Support/Claude/claude-code-sessions'
TZ = dt.timezone(dt.timedelta(hours=8))
STATES = {'running':'正在做','waiting':'等你处理','blocked':'卡住了','pending':'待继续','review':'待核验','done':'已完成','paused':'已暂停','saved':'已留存'}

def read(path, default):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return default

def write(path, obj):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2)); tmp.chmod(0o600); tmp.replace(path)

def connect(path):
    c=sqlite3.connect('file:'+str(path)+'?mode=ro', uri=True, timeout=3); c.row_factory=sqlite3.Row
    return c

def clean(text, limit=1400):
    text = re.sub(r'\[([^\]]+)\]\([^\n]+?\)', r'\1', text or '')
    text = re.sub(r'/Users/[^\s）)。]+', '〔本地记录〕', text)
    text = re.sub(r'om_[a-zA-Z0-9]+', '〔交付回执〕', text)
    return text.strip().replace('**','')[:limit]

def tail_status(path, cache):
    """Read a bounded tail only when changed; never expose tool arguments/results."""
    try: stat=Path(path).stat()
    except OSError: return {'state':'review','detail':'本地执行记录不可读，需打开原聊天核对。','stamp':0}
    signature=[stat.st_mtime_ns,stat.st_size]
    if cache.get('signature')==signature:
        if cache.get('state')=='running' and time.time()-stat.st_mtime>900:
            cache=dict(cache,state='review',detail='超过15分钟没有新记录，运行状态待核实。\n'+cache['detail'])
        return cache
    result={'state':cache.get('state','review'),'detail':cache.get('detail','尚未找到明确的进度记录。'),'stamp':stat.st_mtime,'signature':signature}
    if cache.get('revision'):result['revision']=cache['revision']
    with open(path,'rb') as f:
        start=max(0,stat.st_size-262144);f.seek(start)
        if start:f.readline()
        for line in f:
            try:x=json.loads(line)
            except ValueError:continue
            if x.get('type')!='event_msg':continue
            p=x.get('payload',{});typ=p.get('type');it=p.get('item',{})
            if p.get('turn_id'):result['revision']=p['turn_id']
            if typ=='task_started':result['state']='running'
            if typ in ('task_complete','task_completed'):
                result['state']='review'
                if p.get('last_agent_message'):result['detail']=clean(p['last_agent_message'])
            if typ in ('turn_aborted','task_failed'):result['state']='pending'
            if typ=='item_completed' and isinstance(it,dict) and it.get('type')!='AgentMessage':result['state']='running'
            if typ=='item_completed' and isinstance(it,dict) and it.get('type')=='AgentMessage':
                content='\n'.join(v.get('text','') for v in it.get('content',[]) if v.get('type')=='Text')
                if content:
                    result['detail']=clean(content)
                    result['state']='review' if it.get('phase') in ('final_answer','final') else 'running'
            if typ=='agent_message' and p.get('message'):result['detail']=clean(p['message'])
    if result['state']=='running' and time.time()-stat.st_mtime>900:
        result['state']='review';result['detail']='超过15分钟没有新记录，运行状态待核实。\n'+result['detail']
    return result

def claude_tasks(cache):
    result=[]
    for path in CLAUDE.glob('*/*/local_*.json'):
        signature=path.stat().st_mtime_ns
        old=cache.get('claude:'+path.stem,{})
        if old.get('signature')==signature and time.time()-(old.get('row') or {}).get('updated',0)>900:
            row=old.get('row')
        else:
            meta=read(path,{})
            row=None
            if not meta.get('isArchived') and meta.get('title') and meta.get('sessionId'):
                summary=meta.get('postTurnSummary') or {}
                stamp=int(meta.get('lastActivityAt',meta.get('createdAt',0))/1000)
                state='waiting' if summary.get('needs_action') else 'review'
                detail=clean(summary.get('status_detail','')) or '原会话暂无可读进度。'
                sid=meta.get('cliSessionId','')
                files=list((Path.home()/'.claude/projects').glob('*/'+sid+'.jsonl')) if sid else []
                if files:
                    # Only public assistant text; never tool results, hidden reasoning, or user attachments.
                    with files[0].open('rb') as f:
                        f.seek(max(0,files[0].stat().st_size-131072))
                        for line in f:
                            try:event=json.loads(line)
                            except ValueError:continue
                            if event.get('type')=='assistant':
                                msg=event.get('message',{})
                                text='\n'.join(c.get('text','') for c in msg.get('content',[]) if isinstance(c,dict) and c.get('type')=='text')
                                if text:detail=clean(text)
                                if msg.get('stop_reason')=='end_turn':state='waiting' if summary.get('needs_action') else 'review'
                                elif msg.get('stop_reason')=='tool_use' and time.time()-stamp<900:state='running'
                            elif event.get('type')=='user' and not event.get('isMeta') and time.time()-stamp<900:
                                state='running'
                row=dict(id='claude:'+meta['sessionId'],title=meta['title'],state=state,detail=detail,updated=stamp,source='Claude',thread=meta['sessionId'],url='https://claude.ai/epitaxy/'+meta['sessionId'],next=clean(summary.get('needs_action','')),revision=str(meta.get('latestUserFrameAt','')))
            cache['claude:'+path.stem]={'signature':signature,'row':row}
        if row:
            row=dict(row)
            if row['state']=='running' and time.time()-row['updated']>900:row['state']='review'
            result.append(row)
    return result

def sync():
    now=int(time.time()); config=read(HERE/'config.json',{}); overrides=read(DATA/'overrides.json',{}); cache=read(DATA/'cache.json',{})
    tasks=[]; bound=set(); errors=[];names={};commands=[];project_links={}
    try:
        with connect(CODEX) as c:
            names={r['id']:r['name'] for r in c.execute('select id,name from threads where name is not null')}
    except sqlite3.Error:pass
    try:
        with connect(IVY) as c:
            channels=dict(c.execute('select task_key,channel from task_channels')) if c.execute("select 1 from sqlite_master where name='task_channels'").fetchone() else {}
            for row in c.execute('select * from tasks order by updated desc'):
                r=dict(row); key='ivy:'+r['key']; bound.add(r['thread_id'])
                if r['key'].startswith('desktop_claude_'):continue  # Show the original Claude task, not its transport worker.
                note=c.execute('select m.note,m.updated_ms from messages m join deliveries d on m.id=d.message_id where d.task_key=? order by m.updated_ms desc limit 1',(r['key'],)).fetchone()
                stamp=max(r['updated'] or 0,int((note['updated_ms'] or 0)/1000) if note else 0)
                state={'done':'done','ignored':'saved','running':'running','blocked':'blocked','uncertain':'review','queued':'pending','pending':'pending'}.get(r['status'],'review')
                if state in ('done','saved') and stamp<now-30*86400:continue
                tasks.append(dict(id=key,title=names.get(r['thread_id']) or r['title'],state=state,detail=clean(note['note']) if note else '尚无进度记录。',updated=stamp,source={'grok_cli':'Grok','claude_app':'Claude'}.get(channels.get(r['key']), 'Codex' if r['thread_id'] else 'Ivy'),thread=r['thread_id'] or '',url='',revision=r['turn_id'] or str(stamp)))
            # Incoming tasks are visible even before routing assigns a task key.
            for r in c.execute("select m.id,m.note,m.created_ms from messages m left join deliveries d on m.id=d.message_id where d.message_id is null and m.status in ('pending','queued')"):
                tasks.append(dict(id='inbox:'+r['id'],title='新任务 · 等待分派',state='pending',detail=clean(r['note']),updated=int(r['created_ms']/1000),source='Ivy',thread='',url=''))
            if c.execute("select 1 from sqlite_master where name='desktop_inputs'").fetchone():
                if 'project_id' in {x[1] for x in c.execute('pragma table_info(desktop_inputs)')}:
                    for link in c.execute("select d.task_key,i.project_id from desktop_inputs i join deliveries d on d.message_id=i.id where i.project_id<>'' order by i.created_ms"):
                        project_links['ivy:'+link['task_key']]=link['project_id']
                has_reply='reply' in {x[1] for x in c.execute('pragma table_info(desktop_inputs)')}
                reply_column='i.reply' if has_reply else "''"
                for r in c.execute('''select i.id,i.text,i.target_title,i.created_ms,m.status,m.note,d.task_key,t.thread_id,'''+reply_column+''' as reply
                    from desktop_inputs i join messages m on m.id=i.id left join deliveries d on d.message_id=i.id
                    left join tasks t on t.key=d.task_key order by i.created_ms desc limit 8'''):
                    labels={'pending':'等待分派','routing':'正在分派','queued':'已排队','working':'正在执行','done':'已完成','blocked':'需要处理','ignored':'已留存','uncertain':'待核验'}
                    commands.append({'id':r['id'],'text':r['text'],'target':r['target_title'],'status':labels.get(r['status'],r['status']),'reply':clean(r['note'] or r['reply']),'thread':r['thread_id'] or ''})
    except (OSError,sqlite3.Error) as e:errors.append('Ivy 数据暂时不可读：'+type(e).__name__)
    try:
        with connect(CODEX) as c:
            rows=c.execute("select id,name,title,rollout_path,updated_at,source,thread_source,agent_role from threads where archived=0 order by updated_at desc")
            for r in rows:
                if r['id'] in bound or r['agent_role'] or r['thread_source'] not in ('user',None):continue
                if r['source'] not in ('vscode','cli') or r['title'].startswith('宿主任务渠道：'):continue
                if not r['title'] or r['title'] in ('Heartbeat Dreamer',r['id']):continue
                status=tail_status(r['rollout_path'],cache.get(r['id'],{}));cache[r['id']]=status
                tasks.append(dict(id='codex:'+r['id'],title=r['name'] or r['title'].splitlines()[0],state=status['state'],detail=status['detail'],updated=max(r['updated_at'],int(status['stamp'])),source='Codex',thread=r['id'],url='',revision=status.get('revision','')))
    except (OSError,sqlite3.Error) as e:errors.append('Codex 数据暂时不可读：'+type(e).__name__)
    try:tasks.extend(claude_tasks(cache))
    except (OSError,ValueError,TypeError) as e:errors.append('Claude 数据暂时不可读：'+type(e).__name__)
    original_titles={t['id']:t['title'] for t in tasks}
    registry=read(DATA/'registry.json',{})
    existing={t['id'] for t in tasks}
    known_threads={t.get('thread') for t in tasks if t.get('thread')}
    tasks.extend(v for k,v in registry.items() if k not in existing and (not v.get('thread') or v['thread'] not in known_threads|bound))
    # Explicit progress survives the final response of the same turn, but not a new turn.
    for task in tasks:
        entry=registry.get(task['id'])
        if entry and entry.get('revision')==task.get('revision'):
            task.update({k:v for k,v in entry.items() if k in ('title','state','detail','next','updated')})
    # Explicit, evidence-backed annotations expire if the underlying source changes.
    for task in tasks:
        annotation=config.get('annotations',{}).get(task['id'],{})
        if annotation and task['updated']<=annotation.get('valid_through',0):
            task.update({k:v for k,v in annotation.items() if k in ('state','detail','next','title')})
        ov=overrides.get(task['id'],{})
        if ov.get('source_updated')==task['updated']:
            if ov.get('state') in STATES:task['state']=ov['state']
        task['today']=ov.get('today')==dt.datetime.now(TZ).date().isoformat()
        task.setdefault('next','')
        task['label']=STATES[task['state']]
        # The dashboard must use the exact source name, even if an old manual annotation shortened it.
        if task['id'] in original_titles:task['title']=original_titles[task['id']]
    tasks.sort(key=lambda t:(['running','waiting','blocked','pending','review','paused','done','saved'].index(t['state']),-t['updated']))
    old=read(DATA/'board.json',{})
    if errors:
        present={t['id'] for t in tasks}
        for t in old.get('tasks',[]):
            origin='Ivy' if t['id'].startswith(('ivy:','inbox:')) else t['source']
            if t['id'] not in present and any(origin in e for e in errors):tasks.append(t)
        if not commands and any('Ivy' in e for e in errors):commands=old.get('commands',[])
    health=read(IVY.parent/'health.json',{})
    board={'generated':now,'date':dt.datetime.now(TZ).strftime('%Y年%m月%d日'),'errors':errors,'tasks':tasks,'commands':commands,'ivy_online':now-health.get('time',0)<120,'model':config.get('model','未核实')}
    project_store=read(DATA/'projects.json',EMPTY)
    project_store['assignments']={**project_links,**project_store.get('assignments',{})}
    board['projects']=project_rows(tasks,project_store)
    write(DATA/'cache.json',cache);write(DATA/'board.json',board)
    return board

def main():
    p=argparse.ArgumentParser();p.add_argument('command',nargs='?',default='sync',choices=['sync','mark','today','record']);p.add_argument('--id');p.add_argument('--state',choices=list(STATES));p.add_argument('--off',action='store_true');p.add_argument('--title');p.add_argument('--progress');p.add_argument('--next',default='');p.add_argument('--source',default='手动登记');p.add_argument('--thread',default='');a=p.parse_args()
    DATA.mkdir(parents=True,exist_ok=True);DATA.chmod(0o700)
    with (DATA/'sync.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if a.command=='record':
            if not all([a.id,a.title,a.state,a.progress]):p.error('record requires --id --title --state --progress')
            board=sync();task=next((t for t in board['tasks'] if t['id']==a.id),{})
            registry=read(DATA/'registry.json',{})
            registry[a.id]=dict(id=a.id,title=a.title,state=a.state,detail=a.progress,next=a.next,source=task.get('source',a.source),thread=task.get('thread',a.thread),url='',updated=int(time.time()),revision=task.get('revision','manual'))
            write(DATA/'registry.json',registry)
        elif a.command!='sync':
            board=read(DATA/'board.json',{});task=next(t for t in board.get('tasks',[]) if t['id']==a.id)
            ov=read(DATA/'overrides.json',{});row=ov.setdefault(a.id,{})
            if a.command=='mark':
                if not a.state:p.error('--state required')
                row.update(state=a.state,source_updated=task['updated'])
            else:row['today']='' if a.off else dt.datetime.now(TZ).date().isoformat()
            write(DATA/'overrides.json',ov)
        result=sync();print(json.dumps({'tasks':len(result['tasks']),'errors':result['errors']},ensure_ascii=False))
if __name__=='__main__':main()
