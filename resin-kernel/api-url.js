/** One deployment base for requests, static links and retired entry points.
 * The module URL stays stable when the shell changes its hash route. Node-side
 * test fixtures retain their original paths unless they supply a base. */
export const appBase=new URL('./',import.meta.url);
export const conflictCodes=new Set(['VERSION_CONFLICT','ARCHIVE_READONLY','PROJECTION_READONLY','MOUNT_OWNED','IN_USE','DESIGN_MISSING']);
export function apiUrl(input,base){
  const value=input instanceof URL?input.href:String(input??'');
  if(base===undefined&&appBase.protocol==='file:')return value;
  const root=new URL(base??appBase);
  if(/^(?:data|blob|mailto|javascript):/i.test(value))return value;
  if(/^[a-z][a-z\d+.-]*:/i.test(value)||value.startsWith('//')){
    const absolute=new URL(value,root);
    if(absolute.origin!==root.origin||absolute.pathname.startsWith(root.pathname))return absolute.href;
    return new URL(absolute.pathname.replace(/^\/+/,'' )+absolute.search+absolute.hash,root).href;
  }
  if(value.startsWith(root.pathname)&&root.pathname!=='/')return new URL(value,root.origin).href;
  return new URL(value.replace(/^\/+/,''),root).href;
}
export const staticUrl=apiUrl;

const saveListeners=new Set();let lastSave={state:'saved'},pendingWrites=0,settleTimer,dirty=false,failed=false;
export function onSaveState(listener){saveListeners.add(listener);return ()=>saveListeners.delete(listener);}
export const currentSaveState=()=>({...lastSave});
export function notifySaveState(state,{code,message}={}){
  if(!['saved','saving','dirty','error','conflict'].includes(state))return;
  if(state==='dirty')dirty=true;
  if(state==='saving'){dirty=false;failed=false;}
  if(state==='saved'){dirty=false;failed=false;}
  if(['error','conflict'].includes(state))failed=true;
  const next={state,...state==='conflict'&&conflictCodes.has(code)?{code}:{},...typeof message==='string'&&message?{message}:{}};
  // A generic catch/render message must not erase a coded response from DATA.
  if(lastSave.state==='conflict'&&state==='error')return;
  if(JSON.stringify(next)===JSON.stringify(lastSave))return;
  lastSave=next;for(const listener of saveListeners)listener({...next});
}
export function reportSaveStatus(message,error=false){
  const text=String(message??'');
  if(error){notifySaveState('error',{message:text});return;}
  if(/保存中|正在保存|正在完成/.test(text))notifySaveState('saving');
  else if(/待保存|未保存/.test(text))notifySaveState('dirty');
  else if(/已保存|已读取|保存完成/.test(text))notifySaveState('saved');
}
export async function appFetch(input,options){
  const request=typeof Request!=='undefined'&&input instanceof Request?new Request(apiUrl(input.url),input):apiUrl(input);
  const method=String(options?.method??(typeof Request!=='undefined'&&request instanceof Request?request.method:'GET')).toUpperCase();
  const write=!['GET','HEAD','OPTIONS'].includes(method);
  if(write){clearTimeout(settleTimer);pendingWrites++;notifySaveState('saving');}
  try{
    const response=await globalThis.fetch(request,options);
    if(response.status===409){
      const body=await response.clone?.().json().catch(()=>({}))??{};
      const message=typeof body.error==='string'&&/[\u3400-\u9fff]/.test(body.error)?body.error:'保存遇到冲突，请按提示重新加载后重试。';
      notifySaveState(conflictCodes.has(body.code)?'conflict':'error',{code:body.code,message});
    }else if(write&&!response.ok)notifySaveState('error',{message:'数据服务未完成保存，请重试。'});
    return response;
  }catch(error){if(write)notifySaveState('error',{message:'数据服务暂时无法连接，请重试。'});throw error;}
  finally{if(write){pendingWrites--;if(!pendingWrites&&!failed)settleTimer=setTimeout(()=>{if(!pendingWrites&&!failed)notifySaveState(dirty?'dirty':'saved');},120);}}
}
