import {appFetch} from './api-url.js';
export async function storeAttachment(bytes,name,kind='导入'){
 const response=await appFetch('/api/data-file/attachment?'+new URLSearchParams({name,kind}),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:bytes instanceof Blob?bytes:new Blob([bytes])});
 const result=await response.json();if(!response.ok)throw Error(result.error??'附件保存未完成');return result;
}
export async function fileSource(file,bytes){
 return storeAttachment(bytes??await file.arrayBuffer(),file.name,file.type.startsWith('image/')?'图片':'导入');
}
export function retainExport(bytes,name){if(/\.resindb$/i.test(name))return;storeAttachment(bytes,name,'导出').catch(error=>{const element=document.getElementById('toast');if(element){element.textContent='下载已开始；附件副本未保存：'+error.message;element.hidden=false;}else alert('附件副本未保存：'+error.message);});}
