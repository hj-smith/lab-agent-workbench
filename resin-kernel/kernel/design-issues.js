export function executionIssue(error){
  const detail=String(error?.message??error);
  const missing=/找不到物料[：:]\s*(.+)/.exec(detail);
  return {level:'error',severity:'error',code:'execution-check',message:missing?`执行核对：物料库缺少 ${missing[1]}，请在物料库补建并补充物性。`:`执行核对：${detail}；请打开本实验修正相应物料、参数、步骤或冻结执行版本后重试。`};
}
