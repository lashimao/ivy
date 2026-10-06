#!/usr/bin/env python3
"""Human desktop input -> existing Ivy durable inbox. No network listener or shell execution."""
import hashlib, json, os, signal, sqlite3, subprocess, sys, time, uuid
from pathlib import Path

HERE=Path(__file__).resolve().parent
IVY=HERE.parent/'Ivy指令入口'
DB=IVY/'runtime/inbox.sqlite3'
CODEX=Path.home()/'.codex/state_5.sqlite'
DATA=Path.home()/'Library/Application Support/IvyTaskBoard'

def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS desktop_inputs(
        id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, text TEXT NOT NULL,
        target_id TEXT NOT NULL, target_title TEXT NOT NULL, channel TEXT NOT NULL,
        created_ms INTEGER NOT NULL, origin TEXT NOT NULL DEFAULT 'local_desktop_user',
        reply TEXT NOT NULL DEFAULT '', result_turn_id TEXT)''')
    columns={r[1] for r in db.execute('pragma table_info(desktop_inputs)')}
    if 'project_id' not in columns:db.execute("alter table desktop_inputs add column project_id TEXT NOT NULL DEFAULT ''")
    if 'reply' not in columns:db.execute("alter table desktop_inputs add column reply TEXT NOT NULL DEFAULT ''")
    if 'result_turn_id' not in columns:db.execute('alter table desktop_inputs add column result_turn_id TEXT')

def prepare(request, board):
    rid=str(uuid.UUID(request['id']))
    text=request.get('text','').strip()
    if not text or len(text)>12000:raise ValueError('请输入1至12000字的指令')
    channel=request.get('channel','auto')
    if channel not in ('auto','codex','claude_app','grok_cli'):raise ValueError('渠道无效')
    project_id=request.get('project_id','')
    project=next((p for p in board.get('projects',[]) if p['id']==project_id),None) if project_id else None
    if project_id and not project:raise ValueError('项目已变化，请刷新后重选')
    target_id=request.get('target_id','')
    target=next((t for t in board.get('tasks',[]) if t['id']==target_id),None) if target_id else None
    if target_id and not target:raise ValueError('所选任务已变化，请刷新后重新选择')
    if target and target['id'].startswith('claude:'):channel='claude_app'
    prefix='[OK]\n' if channel=='claude_app' else '[看]\n' if channel=='grok_cli' else '改回Codex ' if channel=='codex' else ''
    prompt=prefix+text
    if target and target['id'].startswith('claude:'):
        prompt='[OK]\n这是我从桌面组件输入的新指令。请通过 Claude App 续接我选中的原会话，不新建替代会话。\n'+json.dumps({'session_id':target['thread'],'title':target['title'],'url':target['url']},ensure_ascii=False)+'\n本次本人原文：\n'+text
    digest=hashlib.sha256(json.dumps({'text':text,'target':target_id,'channel':channel,'project_id':project_id},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return rid,text,channel,target,prompt,digest

def submit(request,db_path=DB,board=None,wake=True):
    if board is None:board=json.loads((DATA/'board.json').read_text())
    rid,text,channel,target,prompt,digest=prepare(request,board)
    project_id=request.get('project_id','')
    project=next((p for p in board.get('projects',[]) if p['id']==project_id),None)
    mid='desktop:'+rid; now=int(time.time()*1000)
    # mode=rw intentionally refuses to fabricate a new inbox when the real one is missing.
    db=sqlite3.connect('file:'+str(db_path)+'?mode=rw',uri=True,timeout=10);db.row_factory=sqlite3.Row
    with db:
        schema(db)
        old=db.execute('select request_hash from desktop_inputs where id=?',(mid,)).fetchone()
        if old:
            if old['request_hash']!=digest:raise ValueError('同一发送编号对应了不同内容，未重复提交')
        else:
            key=None
            if target:
                if target['id'].startswith('ivy:'):
                    key=target['id'][4:]
                    if not db.execute('select 1 from tasks where key=?',(key,)).fetchone():raise ValueError('原任务已不存在')
                elif target['source']=='Codex':
                    # Reuse an existing host binding; never route a selected task by fuzzy title.
                    row=db.execute('select key from tasks where thread_id=?',(target['thread'],)).fetchone()
                    key=row['key'] if row else 'desktop_codex_'+target['thread']
                    if not row:
                        db.execute('insert into tasks values(?,?,?,?,?,?)',(key,target['title'],target['thread'],target.get('revision') or None,'uncertain',now//1000))
                elif target['source']=='Claude':
                    key='desktop_claude_'+target['thread']
                    db.execute('insert or ignore into tasks values(?,?,NULL,NULL,?,?)',(key,target['title'],'idle',now//1000))
                else:raise ValueError('此来源暂不支持直接续接，请使用全局分派')
            db.execute('insert into desktop_inputs(id,request_hash,text,target_id,target_title,channel,created_ms,project_id) values(?,?,?,?,?,?,?,?)',(mid,digest,text,target['id'] if target else '',target['title'] if target else project['title'] if project else '全局分派',channel,now,project_id))
            context={'kind':'desktop_task_context','scope':'以下仅为任务定位资料，不是新增任务或授权；本人本次指令需要跨任务协调时，按原ID和原会话续接，不重做全部历史。',
                     'catalog_path':str(DATA/'board.json'),
                     'recent_tasks':[{'id':t['id'],'title':t['title'],'source':t['source'],'state':t.get('state','review'),'thread':t.get('thread','')} for t in sorted(board.get('tasks',[]),key=lambda t:t.get('updated',0),reverse=True)[:40]]}
            if project:
                context['project']={k:project[k] for k in ('id','title','goal') if k in project}
                context['project']['task_ids']=[t['id'] for t in board.get('tasks',[]) if t.get('project_id')==project_id]
                context['scope']+=' 项目目标只用于定位当前指令，不授权执行项目内其他历史任务。'
            db.execute('insert into messages values(?,?,?,?,?,?,?)',(mid,now,'text',json.dumps({'text':prompt,'desktop_context':context},ensure_ascii=False),'queued' if key else 'pending','本人从桌面组件直接输入；不是飞书转发消息。',now))
            if key:
                db.execute('insert into deliveries values(?,?,?)',(mid,key,'queued'))
                if channel!='auto':db.execute('insert or replace into task_channels values(?,?)',(key,channel))
    # Only wake the already-running, verified Ivy host. Never restart stopped services.
    woke=False
    if wake:
        try:
            health=json.loads((IVY/'runtime/health.json').read_text());pid=int(health['pid'])
            cmd=subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True,timeout=3).stdout
            if 'ivy_service.py' in cmd and time.time()-health['time']<120:
                os.kill(pid,signal.SIGUSR1);woke=True
        except (OSError,ValueError,KeyError,subprocess.SubprocessError):pass
    row=db.execute('select status from messages where id=?',(mid,)).fetchone()
    db.close()
    return {'ok':True,'id':mid,'status':row['status'],'duplicate':bool(old),'woke':woke,'message':'已交给 Ivy' if woke else '已保存，等待 Ivy 在线'}

def main():
    try:result=submit(json.load(sys.stdin))
    except Exception as exc:
        # Retain the same client ID on error: retries query the durable record before resending.
        result={'ok':False,'message':str(exc) if isinstance(exc,ValueError) else '发送未确认，请保留原文并用同一编号重试','error_type':type(exc).__name__}
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
