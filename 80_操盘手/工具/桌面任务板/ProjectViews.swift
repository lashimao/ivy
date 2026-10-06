import SwiftUI
extension Content {
 var projects:[ProjectRow]{model.board?.projects ?? []}
 var selectedProject:ProjectRow?{projects.first{$0.id==projectId}}
 var unassignedCount:Int{tasks.filter{($0.project_id ?? "").isEmpty}.count}
 func projectCard(_ p:ProjectRow)->some View {
  Button{projectId=p.id;target=nil;filter="全部";showHistory=false}label:{
   VStack(alignment:.leading,spacing:10){
    HStack{Text(p.title).font(.system(size:18,weight:.semibold));Spacer();Text(p.label).font(.system(size:10,weight:.medium)).foregroundColor(p.state=="paused" ? .secondary:p.blocked>0 ? .orange:.green);Image(systemName:"chevron.right").font(.caption).foregroundColor(.secondary)}
    Text(p.goal.isEmpty ? "未设置目标":p.goal).font(.system(size:12)).foregroundColor(.secondary).lineLimit(2).multilineTextAlignment(.leading)
    if p.milestone_count>0{ProgressView(value:Double(p.completed),total:Double(p.milestone_count)).tint(Color(red:0.15,green:0.42,blue:0.33));Text("里程碑 \(p.completed) / \(p.milestone_count)").font(.system(size:10)).foregroundColor(.secondary)}
    HStack(spacing:12){if p.running>0{Label("\(p.running) 执行中",systemImage:"circle.fill").foregroundColor(.green)};if p.blocked>0{Text("\(p.blocked) 待处理").foregroundColor(.orange)};Spacer();Text("\(p.total) 条执行记录").foregroundColor(.secondary)}.font(.system(size:10))
   }.padding(16).frame(maxWidth:.infinity,alignment:.leading).background(.white.opacity(0.85)).cornerRadius(16)
  }.buttonStyle(.plain).accessibilityLabel("项目："+p.title)
 }
 func projectHeader(_ p:ProjectRow)->some View {
  VStack(alignment:.leading,spacing:10){
   HStack{Text(p.title).font(.title2).bold();Spacer();Menu{Button("编辑项目"){editId=p.id;editTitle=p.title;editGoal=p.goal;showEditor=true};Button("进行中"){model.projectAction(["action":"state","project_id":p.id,"state":"active"])};Button("暂停项目"){model.projectAction(["action":"state","project_id":p.id,"state":"paused"])};Button("标记项目完成"){model.projectAction(["action":"state","project_id":p.id,"state":"done"])}}label:{Image(systemName:"ellipsis")}.menuStyle(.borderlessButton).frame(width:18)}
   Text(p.goal.isEmpty ? "未设置目标":p.goal).font(.system(size:13)).foregroundColor(.secondary)
   HStack{Text("里程碑").font(.system(size:12,weight:.semibold));Spacer();Text("\(p.completed) / \(p.milestone_count)").font(.caption).foregroundColor(.secondary)}
   ForEach(p.milestones){m in Button{model.projectAction(["action":"milestone_toggle","project_id":p.id,"milestone_id":m.id,"done":!m.done])}label:{HStack(alignment:.top){Image(systemName:m.done ? "checkmark.circle.fill":"circle").foregroundColor(m.done ? .green:.secondary);Text(m.title).strikethrough(m.done).foregroundColor(m.done ? .secondary:.primary);Spacer()}.font(.system(size:12))}.buttonStyle(.plain).disabled(model.editing)}
   HStack{TextField("添加里程碑",text:$milestoneDraft).textFieldStyle(.roundedBorder);Button{model.projectAction(["action":"milestone_add","project_id":p.id,"title":milestoneDraft])}label:{Image(systemName:"plus.circle.fill")}.disabled(model.editing || milestoneDraft.trimmingCharacters(in:.whitespaces).isEmpty)}
   Divider();HStack{Text("执行记录").font(.system(size:12,weight:.semibold));Spacer();Text("\(p.total)").font(.caption).foregroundColor(.secondary)}
  }.padding(14).background(.white.opacity(0.75)).cornerRadius(14)
 }
 var projectEditor:some View {
  VStack(alignment:.leading,spacing:14){Text(editId.isEmpty ? "新建项目":"编辑项目").font(.title2).bold();TextField("项目名称",text:$editTitle).textFieldStyle(.roundedBorder);Text("目标").font(.caption).foregroundColor(.secondary);TextEditor(text:$editGoal).font(.body).frame(height:100).border(.gray.opacity(0.2));if !model.error.isEmpty{Text(model.error).font(.caption).foregroundColor(.red)};HStack{Button("取消"){showEditor=false};Spacer();Button("保存"){model.projectAction(["action":editId.isEmpty ? "create":"edit","project_id":editId,"title":editTitle,"goal":editGoal])}.buttonStyle(.borderedProminent).disabled(model.editing || editTitle.trimmingCharacters(in:.whitespaces).isEmpty)}}.padding(24).frame(width:360)
 }
}
