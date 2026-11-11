const branch=source=>/(?:后处理|分支|支路)\s*([AB])(?:\b|$)|\b(Hep|MeOH)\b/i.exec(source?.sheet??'')?.slice(1).find(Boolean)?.toUpperCase();
export const distinctBranchSources=(a,b)=>!!(branch(a)&&branch(b)&&branch(a)!==branch(b)&&a.file===b.file);
export const branchRecordConflict=conflict=>distinctBranchSources(conflict.workbookSource,conflict.supplementarySource??conflict.archiveSource);
