"""Explicit projects and milestones. Session counts never stand in for goal progress."""
import copy, time, uuid

EMPTY={'version':1,'projects':[],'assignments':{}}
STATES={'active','paused','done'}

def apply(store, request, task_ids):
    s=copy.deepcopy(store);s.setdefault('projects',[]);s.setdefault('assignments',{})
    action=request.get('action');pid=request.get('project_id','')
    project=next((p for p in s['projects'] if p['id']==pid),None)
    def text(key,limit):
        value=request.get(key,'')
        if not isinstance(value,str) or len(value.strip())>limit:raise ValueError('内容长度无效')
        return value.strip()
    if action=='create':
        title=text('title',80)
        if not title:raise ValueError('请输入项目名称')
        pid='project:'+uuid.uuid4().hex
        s['projects'].append({'id':pid,'title':title,'goal':text('goal',1000),'state':'active','milestones':[],'updated':int(time.time())})
    elif action=='assign':
        tid=request.get('task_id')
        if tid not in task_ids:raise ValueError('执行记录已变化，请刷新')
        if pid and not project:raise ValueError('项目不存在')
        s['assignments'][tid]=pid
    else:
        if not project:raise ValueError('项目不存在')
        if action=='edit':
            title=text('title',80)
            if not title:raise ValueError('请输入项目名称')
            project.update(title=title,goal=text('goal',1000))
        elif action=='state':
            state=request.get('state')
            if state not in STATES:raise ValueError('项目状态无效')
            project['state']=state
        elif action=='milestone_add':
            title=text('title',160)
            if not title:raise ValueError('请输入里程碑')
            project.setdefault('milestones',[]).append({'id':uuid.uuid4().hex,'title':title,'done':False})
        elif action=='milestone_toggle':
            item=next((m for m in project.get('milestones',[]) if m['id']==request.get('milestone_id')),None)
            if not item:raise ValueError('里程碑不存在')
            if not isinstance(request.get('done'),bool):raise ValueError('完成状态无效')
            item['done']=request['done']
        else:raise ValueError('操作无效')
        project['updated']=int(time.time())
    return s,pid

def project_rows(tasks, store):
    projects=store.get('projects',[]); valid={p['id'] for p in projects};mapping=store.get('assignments',{})
    for t in tasks:t['project_id']=mapping.get(t['id'],'') if mapping.get(t['id']) in valid else ''
    rows=[]
    for p in projects:
        members=[t for t in tasks if t['project_id']==p['id']]
        running=sum(t['state']=='running' for t in members)
        blocked=sum(t['state'] in ('blocked','waiting') for t in members)
        milestones=p.get('milestones',[]);done=sum(bool(m['done']) for m in milestones)
        state=p.get('state','active')
        label={'paused':'已暂停','done':'已完成'}.get(state,'执行中' if running else '待处理' if blocked else '进行中')
        rows.append(dict(id=p['id'],title=p['title'],goal=p.get('goal',''),state=state,label=label,running=running,blocked=blocked,total=len(members),updated=max([p.get('updated',0)]+[t['updated'] for t in members]),milestones=milestones,completed=done,milestone_count=len(milestones)))
    rows.sort(key=lambda p:(p['state']!='active',-p['running'],-p['updated']))
    return rows
