// Derived quantities have explicit semantic identities. Labels are presentation only.
const keys={expectedProduct:['预计产品','整批预计产品','预计产品(g)'],branchExpectedProduct:['每支预计产品','每路预计产品','每支预计产品(g)','每路预计产品(g)'],theoreticalResin:['理论净树脂','总理论净树脂(g)','理论树脂'],theoreticalWater:['理论生成水'],thfVolume:['整批THF','THF体积','THF稀释体积'],monoBMmol:['MonB摩尔数','MonB_mmol'],monoARatio:['MonB:MonA摩尔比'],precipitationRatio:['沉降MeOHTHF'],thfPerProduct:['THF预计产品(mLg)'],yieldFraction:['预计分离计划系数','预计分离收率']};
export function roleVar(vars,role){return vars.find(v=>v.role===role)??(keys[role]??[]).map(key=>vars.find(v=>v.key===key)).find(Boolean);}
export function assignVarRoles(vars){for(const role of Object.keys(keys)){const v=roleVar(vars,role);if(v&&!v.role)v.role=role;if(v&&role==='yieldFraction')v.label='计划分离系数';}return vars;}
export function roleValue(vars,values,role){const v=roleVar(vars,role);return v?values.get(v.key):null;}
