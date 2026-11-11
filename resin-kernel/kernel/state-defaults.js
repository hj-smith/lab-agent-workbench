// Persistence-safe defaults shared by migration, the server and every client load.
const arrays=['experiments','materials','metricDefinitions','templates','snapshots','audit','agingStudies','trash','referenceIndex','dataNotes'];
const objects=['taskStatus','uiPrefs'];
export function ensureStateDefaults(state) {
  if(!state||typeof state!=='object'||Array.isArray(state))throw Error('实验数据结构无效，请从备份核对。');
  state.schemaVersion??=1;
  for(const key of arrays)if(state[key]==null)state[key]=[];
  for(const key of objects)if(state[key]==null)state[key]={};
  state.comparisonReports??=[];
  state.comparisonWorkspace??={};
  // Earlier UI adapters persisted their category translation. Restore only an
  // exact, documented translation with an explicit original value.
  const translated={溶剂:'solvent',单体B:'monoB',单体A:'monoA',酸:'acid',碱:'base',催化剂:'catalyst',添加剂:'additive'};
  for(const material of state.materials)if(translated[material.sourceKind]===material.kind)material.kind=material.sourceKind;
  return state;
}

// Full backups carry these alongside state, never inside the persisted UI
// state. Older importers accidentally retained both large envelope copies.
// Only remove the two reserved top-level names; archives and free text stay as-is.
export function stripBackupEnvelope(state) {
  delete state.kernel;
  delete state.integration;
  return state;
}
