// Fixed metadata from the SDK's current runtime config, never from a disk fallback.
const invalid=()=>new Error('model runtime contract unavailable');
const object=value=>{if(!value||typeof value!=='object'||Array.isArray(value))throw invalid();return value;};
const number=value=>Number.isSafeInteger(value)&&value>=0&&value<=10000000;
const get=(value,key,fallback)=>Object.hasOwn(value,key)?value[key]:fallback;
export function readRuntimeModel(config, metadata) {
  const agents=config?.agents?.list;
  if(!Array.isArray(agents)||agents.length!==1||agents[0]?.id!=='pixel')throw invalid();
  const agent=agents[0],selected=agent.model?.primary??agent.model;
  if(typeof selected!=='string'||!selected.includes('/'))throw invalid();
  const cut=selected.indexOf('/'),provider=selected.slice(0,cut),runtimeId=selected.slice(cut+1);
  if(!['ods-local','ods-gateway'].includes(provider))throw invalid();
  const rows=config?.models?.providers?.[provider]?.models;
  if(!Array.isArray(rows))throw invalid();
  const matches=rows.filter(row=>row?.id===runtimeId);if(matches.length!==1)throw invalid();
  const row=matches[0],plugin=object(config?.plugins?.entries?.['pixel-ods']);
  if(plugin.enabled===false)throw invalid();
  const settings=object(plugin.config??{});
  if('managedProvider' in settings)throw invalid();
  let model=runtimeId;
  if(provider==='ods-gateway') {
    const label={'ods/current':'Current',default:'Default'}[runtimeId];
    const match=label&&typeof row.name==='string'&&row.name.match(new RegExp(`^ODS ${label} \\((.+)\\)$`));
    if(!match)throw invalid();model=match[1];
  } else if(row.name!==`ODS Local ${model}`)throw invalid();
  if(typeof model!=='string'||model.length>256||!model.length||/[\r\n]/.test(model)||!/^[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]*$/.test(model))throw invalid();
  const contract={model,contextLength:row.contextWindow,maxTokens:row.maxTokens,reasoning:row.reasoning};
  if(!number(contract.contextLength)||contract.contextLength<4096||!number(contract.maxTokens)||contract.maxTokens<1||contract.maxTokens>contract.contextLength||typeof contract.reasoning!=='boolean')throw invalid();
  if('modelRouteFingerprint' in settings) {
    const value=settings.modelRouteFingerprint;
    if(provider!=='ods-gateway'||typeof value!=='string'||value.length!==64||!/^[a-f0-9]{64}$/.test(value))throw invalid();
    contract.routeFingerprint=value;
  }
  const defaults=config.agents.defaults??{},compaction=defaults.compaction??{};
  const layers=[defaults.params??{},defaults.models?.[selected]?.params??{},agent.params??{}];
  let output=contract.maxTokens,declared=false,resolved=false;
  for(const value of layers)for(const key of ['maxTokens','max_completion_tokens','max_tokens']) {
    const params=object(value);if(!Object.hasOwn(params,key))continue;
    declared=true;
    if(typeof params[key]==='number'&&Number.isFinite(params[key])&&params[key]>=0) {
      output=params[key];resolved=true;break;
    }
  }
  if(declared&&!resolved)throw invalid();
  const limits={contextTokens:get(agent,'contextTokens',get(defaults,'contextTokens',contract.contextLength)),
    maxOutputTokens:output,
    pluginContext:get(settings,'modelContextWindow',contract.contextLength),
    reserveTokens:compaction.reserveTokens??null,reserveTokensFloor:compaction.reserveTokensFloor??null,keepRecentTokens:compaction.keepRecentTokens??null};
  if(Object.values(limits).some(value=>value!==null&&!number(value)))throw invalid();
  if(['contextTokens','maxOutputTokens','pluginContext'].some(key=>!number(limits[key])||limits[key]<1))throw invalid();
  return {schemaVersion:1,source:'current-model-contract',pid:metadata.pid,revision:metadata.revision,
    observedAt:metadata.observedAt,contract,limits};
}
