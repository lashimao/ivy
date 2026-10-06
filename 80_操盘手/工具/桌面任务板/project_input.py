#!/usr/bin/env python3
"""Local project edits; no model calls and no task execution."""
import fcntl,json,os,sys
from pathlib import Path
from project_model import apply,EMPTY
DATA=Path(os.environ.get('IVY_BOARD_DATA',str(Path.home()/'Library/Application Support/IvyTaskBoard')))

def main():
    DATA.mkdir(parents=True,exist_ok=True);DATA.chmod(0o700)
    with (DATA/'sync.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        path=DATA/'projects.json';store=json.loads(path.read_text()) if path.exists() else EMPTY
        board=json.loads((DATA/'board.json').read_text())
        updated,pid=apply(store,json.load(sys.stdin),{t['id'] for t in board.get('tasks',[])})
        tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(updated,ensure_ascii=False,indent=2));tmp.chmod(0o600);tmp.replace(path)
    print(json.dumps({'ok':True,'project_id':pid,'message':'已保存'},ensure_ascii=False))
if __name__=='__main__':
    try:main()
    except Exception as e:print(json.dumps({'ok':False,'message':str(e) if isinstance(e,ValueError) else '保存失败，请重试'},ensure_ascii=False))
