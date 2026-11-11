import {stableUid,aliases} from './identity.js';

const copy=structuredClone,list=value=>Array.isArray(value)?value:[];
const identity=(source,kind,id,code)=>({schemaVersion:'1.0',source_instance_uid:source,uid:stableUid(source,kind+':'+id),revision:1,code:String(code??id),aliases:aliases([],String(id)),updated_at:null});

/** Preserve the committed source envelope. The tree supplies structure; prose,
 * codes and the source's redundant flat index never invent experiment links. */
export function buildWorkspaceRecords(state={},roadmap={},source){
  const map=copy(state.researchMap??roadmap??{}),research_node=[],aging_study=[],nodeIds=new Set(),studyIds=new Set();
  const root=map.sourceModel?.root??map.root;
  const visit=(node,parent=null,depth=0)=>{
    if(!node||typeof node!=='object')return;
    if(node.id==null||String(node.id)===''||nodeIds.has(String(node.id)))throw Error('研发路线节点 ID 缺失或重复，未生成猜测的身份');
    nodeIds.add(String(node.id));const base=identity(source,'research_node',node.id,node.id);
    research_node.push({...base,node_id:node.id,title:node.title??'',parent_uid:parent?stableUid(source,'research_node:'+parent.id):null,parent_id:parent?.id??null,depth,experiment_ids:copy(list(node.experimentIds)),external_refs:copy(list(node.externalRefs)),node:copy(node),...parent?{}:{map:copy(map)}});
    for(const child of list(node.children))visit(child,node,depth+1);
  };
  if(root)visit(root);
  for(const study of list(state.agingStudies)){
    if(study?.id==null||String(study.id)===''||studyIds.has(String(study.id)))throw Error('老化研究 ID 缺失或重复，未生成猜测的身份');
    studyIds.add(String(study.id));aging_study.push({...copy(study),...identity(source,'aging_study',study.id,study.code??study.name??study.id),study_id:study.id,experiment_id:copy(study.experimentId??null),sample_id:copy(study.sampleId??null),study:copy(study)});
  }
  return {map,research_node,aging_study};
}
