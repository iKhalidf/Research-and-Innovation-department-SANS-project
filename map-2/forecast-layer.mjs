const LOCAL_REPLAY = '/forecast-data/replay.json';
const PUBLIC_REPLAY = new URL('./forecast-example.json', import.meta.url);
const LOOPBACK = ['127.0.0.1', 'localhost', '[::1]'];
const ISO_HOUR = 3_600_000;
const HOUR_COUNT = 12;
const TOLERANCE = 2;
const isCount = value => Number.isSafeInteger(value) && value >= 0;
const num = new Intl.NumberFormat('en-US', {maximumFractionDigits: 1});
const precise = new Intl.NumberFormat('en-US', {maximumFractionDigits: 2});
const utcDate = new Intl.DateTimeFormat('en-GB', {day:'numeric',month:'long',year:'numeric',timeZone:'UTC'});

export function validateReplay(replay) {
 const protocol=replay?.protocol;
 const source=replay?.source;
 const model=replay?.model;
 const start=Date.parse(replay?.date_utc+'T00:00:00Z');
 if(replay?.schema_version!==1 || !Number.isFinite(start)
   || new Date(start).toISOString().slice(0,10)!==replay.date_utc
   || !['a2','synthetic'].includes(source?.kind) || !/^[a-f0-9]{64}$/.test(source?.sha256)
   || model?.id!=='amazon/chronos-2' || !/^[a-f0-9]{40}$/.test(model?.revision)
   || typeof model.runtime_version!=='string'
   || protocol?.initial_context_hours!==HOUR_COUNT || protocol.horizon_hours!==1
   || protocol.tolerance_departures!==TOLERANCE || protocol.baseline!=='previous_hour'
   || !Array.isArray(replay.context) || replay.context.length!==HOUR_COUNT
   || !Array.isArray(replay.hours) || replay.hours.length!==HOUR_COUNT){
  throw Error('Invalid forecast replay format.');
 }
 const rows=[...replay.context,...replay.hours];
 for(const [i,row] of rows.entries()){
  const expected=new Date(start+i*ISO_HOUR).toISOString().replace('.000Z','Z');
  if(row?.hour_utc!==expected || !isCount(row.actual)
    || (i>=HOUR_COUNT && (!isCount(row.predicted) || row.baseline!==rows[i-1].actual || row.issued_at_utc!==expected))){
   throw Error('Forecast replay has an invalid hourly record.');
  }
 }
 for(const [key,prediction] of [['model','predicted'],['baseline','baseline']]){
  const score=replay.scores?.[key];
  const errors=replay.hours.map(hour=>Math.abs(hour[prediction]-hour.actual));
  const within=errors.filter(error=>error<=TOLERANCE).length;
  const mae=errors.reduce((sum,error)=>sum+error,0)/HOUR_COUNT;
  if(score?.total!==HOUR_COUNT || score.within_tolerance!==within
    || score.mae!==Number(mae.toFixed(2)) || score.within_tolerance_pct!==Number((100*within/HOUR_COUNT).toFixed(2))){
   throw Error('Forecast replay has invalid test scores.');
  }
 }
 return replay;
}

export async function loadReplay({fetcher=fetch, allowLocal=LOOPBACK.includes(globalThis.location?.hostname)}={}) {
 if(allowLocal){
  const local=await fetcher(LOCAL_REPLAY,{cache:'no-store'});
  if(local.ok)return validateReplay(await local.json());
  if(local.status!==404)throw Error('Local forecast replay could not be loaded.');
 }
 const response=await fetcher(PUBLIC_REPLAY);
 if(!response.ok)throw Error('No forecast replay is available. Open the local A2 viewer with an exported replay.');
 const replay=validateReplay(await response.json());
 if(replay.source.kind!=='synthetic')throw Error('The public forecast example must use synthetic counts.');
 return replay;
}

export function forecastImpact(count,record) {
 return {
  fuelKg: count*record.to10k.fuel_kg,
  co2Kg: count*record.to10k.co2_kg,
  residents70: count ? (record.residents?.[70]??0) : 0,
 };
}

const el=(tag,text,style)=>{
 const node=document.createElement(tag);
 if(text!==undefined)node.textContent=text;
 if(style)node.className=style;
 return node;
};
const timeRange=hour=>{
 const end=new Date(Date.parse(hour)+ISO_HOUR).toISOString();
 return hour.slice(11,16)+'–'+end.slice(11,16)+' UTC';
};

export function mountForecast({sidebar,viewport,scenario}) {
 let enabled=false,replay=null,loading=null,index=0,revealed=0;
 let catalog=null,record=null,overlay='off',impactCall=0;
 const panel=el('section',undefined,'forecast-panel');
 panel.setAttribute('aria-label','Forecast Replay');
 panel.hidden=true;
 const notice=el('p',undefined,'forecast-source');
 notice.setAttribute('role','status');
 panel.append(el('h2','Forecast Replay'),notice);

 const content=el('div');
 content.hidden=true;
 const progress=el('p',undefined,'forecast-progress');
 const interval=el('h3',undefined,'forecast-interval');
 const values=el('div',undefined,'scenario-tiles');
 const measure=label=>{
  const tile=el('div',undefined,'scenario-tile');
  const value=el('strong');
  tile.append(el('small',label),value);
  values.append(tile);
  return value;
 };
 const predicted=measure('CHRONOS-2');
 const baseline=measure('PREVIOUS HOUR');
 const detected=el('p',undefined,'forecast-detected');
 detected.setAttribute('aria-live','polite');
 const actions=el('div',undefined,'forecast-actions');
 const step=el('button','Reveal actual count');
 const restart=el('button','Restart replay');
 step.type=restart.type='button';
 actions.append(step,restart);
 content.append(progress,interval,values,detected,actions);

 const assumptions=el('fieldset',undefined,'forecast-assumptions');
 assumptions.append(el('legend','Impact assumptions'),el('p','Apply the forecast count to one runway, route and aircraft.'));
 function choice(label,change){
  const field=el('label',label);
  const select=el('select');
  select.onchange=change;
  field.append(select);
  assumptions.append(field);
  return select;
 }
 function options(select,values,selected){
  select.replaceChildren(...values.map(value=>{
   const option=el('option',value);
   option.value=value;
   return option;
  }));
  if(values.includes(selected))select.value=selected;
 }
 const runway=choice('Runway',syncSids);
 const sid=choice('Departure route',syncAircraft);
 const aircraft=choice('Aircraft',selectScenario);
 assumptions.disabled=true;
 const impactMessage=el('p',undefined,'forecast-impact-message');
 impactMessage.setAttribute('role','status');
 const compactImpact=el('div',undefined,'forecast-compact-impact');
 const summary=el('section',undefined,'forecast-score');
 summary.setAttribute('aria-live','polite');
 const history=el('details',undefined,'forecast-history');
 const table=el('table');
 history.append(el('summary','Revealed hours'),table);
 const method=el('p',undefined,'forecast-method');
 content.append(assumptions,impactMessage,compactImpact,summary,history,method);
 panel.append(content);
 sidebar.append(panel);

 const impactCard=el('section',undefined,'scenario-card forecast-impact-card');
 impactCard.setAttribute('aria-label','Estimated hourly impact');
 impactCard.hidden=true;
 viewport.append(impactCard);

 function renderImpact(){
  const request=++impactCall;
  const visible=enabled&&Boolean(replay&&record);
  impactCard.hidden=compactImpact.hidden=!visible;
  if(!visible){
   overlay='off';
   if(enabled)scenario.showForecast(null,impactCard);
   return;
  }
  const count=replay.hours[index].predicted;
  const impact=forecastImpact(count,record);
  for(const target of [impactCard,compactImpact]){
   const totals=el('div',undefined,'scenario-tiles');
   for(const [label,value] of [['FUEL TO 10,000 FT',impact.fuelKg],['CO₂',impact.co2Kg]]){
    const tile=el('div',undefined,'scenario-tile');
    tile.append(el('small',label),el('strong',num.format(value)+' kg'));
    totals.append(tile);
   }
   const description=count
    ? num.format(impact.residents70)+' estimated residents inside the per-departure 70 dB LAmax contour.'
    : 'Zero modelled departures for this hour.';
   target.replaceChildren(
    el('h3','Estimated hourly impact'),
    el('p',record.runway+' · '+record.sid+' · '+record.aircraft),
    totals,
    el('p',description),
   );
  }
  impactMessage.textContent='';
  overlay='off';
  scenario.showForecast(count>0?record:null,impactCard).then(shown=>{
   if(request===impactCall)overlay=shown?'visible':'off';
  }).catch(()=>{
   if(request===impactCall){overlay='error';impactMessage.textContent='The scenario noise contours could not be loaded.'}
  });
 }
 function selectScenario(){
  record=catalog.scenarios.find(item=>item.runway===runway.value&&item.sid===sid.value&&item.aircraft===aircraft.value);
  renderImpact();
 }
 function syncAircraft(){
  options(aircraft,[...new Set(catalog.scenarios.filter(item=>item.runway===runway.value&&item.sid===sid.value).map(item=>item.aircraft))],aircraft.value);
  selectScenario();
 }
 function syncSids(){
  options(sid,[...new Set(catalog.scenarios.filter(item=>item.runway===runway.value).map(item=>item.sid))],sid.value);
  syncAircraft();
 }
 async function loadAssumptions(){
  try{
   catalog=await scenario.getIndex();
  }catch{
   if(enabled)impactMessage.textContent='Scenario data could not be loaded.';
   return;
  }
  if(!enabled)return;
  options(runway,[...new Set(catalog.scenarios.map(item=>item.runway))],runway.value||'34C');
  assumptions.disabled=false;
  syncSids();
 }

 function renderHistory(){
  history.hidden=revealed===0;
  const thead=el('thead');
  const columns=el('tr');
  for(const label of ['Hour (UTC)','Chronos-2','Baseline','Actual']){
   const cell=el('th',label);
   cell.scope='col';
   columns.append(cell);
  }
  thead.append(columns);
  const tbody=el('tbody');
  for(const hour of replay.hours.slice(0,revealed)){
   const row=el('tr');
   for(const value of [hour.hour_utc.slice(11,16),hour.predicted,hour.baseline,hour.actual])row.append(el('td',String(value)));
   tbody.append(row);
  }
  table.replaceChildren(thead,tbody);
 }
 function renderSummary(){
  summary.hidden=revealed!==replay.hours.length;
  if(summary.hidden)return;
  summary.replaceChildren(el('h3','One-hour-ahead replay scores'));
  for(const [key,label] of [['model','Chronos-2'],['baseline','Previous hour']]){
   const score=replay.scores[key];
   const hitRate=score.within_tolerance+' of '+score.total+' ('+precise.format(score.within_tolerance_pct)+'%) within ±'+TOLERANCE+' departures';
   summary.append(el('p',label+' · MAE '+precise.format(score.mae)+' departures/hour · '+hitRate));
  }
 }
 function renderHour(){
  const row=replay.hours[index];
  const shown=revealed>index;
  const complete=revealed===replay.hours.length;
  progress.textContent='Test hour '+(index+1)+' of '+replay.hours.length;
  interval.textContent=timeRange(row.hour_utc);
  predicted.textContent=num.format(row.predicted);
  baseline.textContent=num.format(row.baseline);
  detected.textContent=shown?(replay.source.kind==='a2'?'A2-detected departures: ':'Synthetic actual count: ')+num.format(row.actual):'';
  step.textContent=complete?'Replay complete':shown?'Next hour':'Reveal actual count';
  step.disabled=complete;
  restart.hidden=index===0&&!shown;
  renderHistory();
  renderSummary();
 }
 step.onclick=()=>{
  if(!replay||revealed===replay.hours.length)return;
  if(revealed===index){revealed++;renderHour();return}
  index++;
  renderHour();
  renderImpact();
 };
 restart.onclick=()=>{
  index=revealed=0;
  history.open=false;
  renderHour();
  renderImpact();
  step.focus();
 };

 function showReplay(){
  const synthetic=replay.source.kind==='synthetic';
  const date=utcDate.format(new Date(replay.date_utc+'T00:00:00Z'));
  notice.textContent=(synthetic?'Synthetic example':'One-hour-ahead retrospective replay')+' · '+date;
  notice.classList.toggle('synthetic',synthetic);
  notice.classList.remove('error');
  content.hidden=false;
  method.textContent=synthetic
   ? 'Chronos-2 forecasts for invented counts. These scores demonstrate the replay and do not measure airport performance.'
   : 'Retrospective single-day test using finalized A2 detections. Provider partitions establish file coverage. A2 detections may differ from airport movement totals.';
  renderHour();
  if(record)renderImpact();
  else loadAssumptions();
 }
 function setMode(active){
  enabled=active;panel.hidden=!active;
  if(!active){impactCall++;overlay='off';impactCard.hidden=true;return}
  if(replay){showReplay();return}
  notice.textContent='Loading forecast replay…';
  notice.classList.remove('error');
  loading??=loadReplay().then(data=>{
   replay=data;
   if(enabled)showReplay();
  }).catch(error=>{
   if(enabled){notice.textContent=error.message;notice.classList.add('error')}
  }).finally(()=>{loading=null});
 }
 return {
  setMode,
  getState:()=>({
   enabled,source:replay?.source.kind??null,model:replay?.model.id??null,
   hour:replay?.hours[index].hour_utc??null,predicted:replay?.hours[index].predicted??null,
   baseline:replay?.hours[index].baseline??null,actual:replay&&revealed>index?replay.hours[index].actual:null,
   revealed,total:replay?.hours.length??0,
   complete:Boolean(replay&&revealed===replay.hours.length),recordId:record?.id??null,overlay,
  }),
 };
}
