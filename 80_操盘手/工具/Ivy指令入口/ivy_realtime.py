#!/usr/bin/env python3
"""Independent zero-model ingress: Feishu WebSocket plus 2-second fallback."""
import fcntl,json,os,queue,re,signal,subprocess,threading,time
from pathlib import Path
import ivy_inbox as inbox
from ivy_service import setup
ROOT=Path(__file__).resolve().parent


def accept(db, raw, source):
    cfg=inbox.meta(db)
    message=raw.get('message',{})
    sender=raw.get('sender',{})
    sender_id=sender.get('sender_id',{}).get('open_id') or sender.get('id')
    if message.get('chat_id')!=cfg['chat'] or sender_id!=cfg['sender'] or sender.get('sender_type')!='user':return False
    created=int(message['create_time'])
    if created<int(cfg['started_ms']):return False
    mid=message['message_id']
    with db:
        inserted=db.execute('INSERT OR IGNORE INTO messages(id,created_ms,kind,content,updated_ms) VALUES(?,?,?,?,?)',
            (mid,created,message['message_type'],message['content'],int(time.time()*1000))).rowcount
        db.execute('INSERT OR IGNORE INTO ingress_receipts VALUES(?,?,?,?)',(mid,source,int(time.time()*1000),created))
    return bool(inserted)


def wake():
    try:
        h=json.loads((ROOT/'runtime/health.json').read_text());pid=h['pid']
        command=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout
        if str(ROOT/'ivy_service.py') in command:os.kill(pid,signal.SIGUSR1)
    except (OSError,ValueError,KeyError):pass


def websocket(events,status):
    try:
        import asyncio
        asyncio.set_event_loop(asyncio.new_event_loop())
        import lark_oapi as lark
        import logging
        # The SDK's connection messages can contain signed URLs. Don't log them.
        logging.getLogger('Lark').disabled=True
        config=inbox.load_config()
        def receive(data):
            payload=json.loads(lark.JSON.marshal(data))
            events.put(payload.get('event',{}))
        handler=lark.EventDispatcherHandler.builder('','').register_p2_im_message_receive_v1(receive).build()
        client=lark.ws.Client(config['FEISHU_APP_ID'],config['FEISHU_APP_SECRET'],event_handler=handler,log_level=lark.LogLevel.ERROR)
        status['ws']='connecting'
        # SDK lifecycle checked via its transport state; model work never runs here.
        status['_client']=client
        client.start()
    except Exception as e:
        status['ws']='unavailable:'+type(e).__name__


def main():
    db=inbox.database();setup(db)
    db.execute('CREATE TABLE IF NOT EXISTS ingress_receipts(message_id TEXT PRIMARY KEY,source TEXT,received_ms INTEGER,created_ms INTEGER)');db.commit()
    lock=open(ROOT/'runtime/realtime.lock','w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    events=queue.Queue();state={'ws':'starting','ws_events':0,'polls':0,'errors':0}
    threading.Thread(target=websocket,args=(events,state),daemon=True).start()
    next_poll=0;next_wake=0;last_health=0;backoff=2
    while True:
        now=time.monotonic()
        while True:
            try:event=events.get_nowait()
            except queue.Empty:break
            try:
                if accept(db,event,'websocket'):state['ws_events']+=1
                state['last_ws_event']=int(time.time())
            except Exception as e:state['last_error']=type(e).__name__;state['errors']+=1
        if now>=next_poll:
            try:
                before=db.execute('SELECT coalesce(max(rowid),0) FROM messages').fetchone()[0]
                inbox.collect(db);state['polls']+=1;backoff=2
                with db:
                    for row in db.execute('SELECT id,created_ms,updated_ms FROM messages WHERE rowid>?',(before,)).fetchall():
                        db.execute('INSERT OR IGNORE INTO ingress_receipts VALUES(?,?,?,?)',(row['id'],'poll',row['updated_ms'],row['created_ms']))
            except Exception as e:
                state['last_error']=type(e).__name__;state['errors']+=1;backoff=min(30,backoff*2)
            next_poll=time.monotonic()+backoff
        # Every inbound message, including tests and greetings, is routed to Codex.
        if db.execute("SELECT count(*) FROM messages WHERE status='pending'").fetchone()[0] and now>=next_wake:
            wake();next_wake=now+1
        if now-last_health>=1:
            public={k:v for k,v in state.items() if not k.startswith('_')}
            client=state.get('_client')
            if client is not None:
                public['ws_connected']=getattr(client,'_conn',None) is not None
            public.update(pid=os.getpid(),time=int(time.time()),fallback_interval_seconds=2,model_calls=0)
            temp=ROOT/'runtime/realtime-health.tmp';temp.write_text(json.dumps(public));temp.replace(ROOT/'runtime/realtime-health.json')
            last_health=now
        time.sleep(.1)

if __name__=='__main__':main()
