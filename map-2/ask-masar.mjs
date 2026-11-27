const LOCAL = new Set(['127.0.0.1', 'localhost']);
const endpoint = '/ask-masar';
const levels = [60, 65, 70];
const modelLabels = {
 'openai/gpt-oss-20b': 'GPT-OSS 20B',
 'openai/gpt-oss-120b': 'GPT-OSS 120B',
 'qwen3.5:4b': 'Qwen3.5‑4B',
 'qwen3.5:2b-q4_K_M': 'Qwen3.5‑2B',
 'qwen2.5:1.5b': 'Qwen2.5‑1.5B',
};
const format = new Intl.NumberFormat('en-US', {maximumFractionDigits: 1});
const signed = new Intl.NumberFormat('en-US', {maximumFractionDigits: 1, signDisplay: 'exceptZero'});
const departureCount = {name: 'count', label: 'Departures', min: 1, max: 1000000, step: 1, value: 20};
const weight = {min: 0.1, max: 100, step: 0.1};
const temperature = {min: -10, max: 60, step: 0.1};
const guidedPrompts = [
 {
  label: 'Estimate fuel',
  fields: [departureCount, {...weight, name: 'mass', label: 'Takeoff weight (% of max)', value: 80}],
  cases: ({count, mass}) => [{count, mass_pct: mass}],
 },
 {
  label: 'Compare takeoff weights',
  fields: [departureCount,
   {...weight, name: 'first', label: 'Case 1 weight (% of max)', value: 75},
   {...weight, name: 'second', label: 'Case 2 weight (% of max)', value: 80}],
  cases: ({count, first, second}) => [{count, mass_pct: first}, {count, mass_pct: second}],
 },
 {
  label: 'Compare noise by temperature',
  fields: [
   {...temperature, name: 'first', label: 'Case 1 temperature (°C)', value: 30},
   {...temperature, name: 'second', label: 'Case 2 temperature (°C)', value: 38}],
  cases: ({first, second}) => [{temperature_c: first}, {temperature_c: second}],
 },
];

const make = (tag, text, css) => {
 const element = document.createElement(tag);
 if (text !== undefined) element.textContent = text;
 if (css) element.className = css;
 return element;
};

async function request(path, body) {
 const options = body ? {
  method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
 } : {cache: 'no-store'};
 const response = await fetch(endpoint + path, options);
 const payload = await response.json();
 if (!response.ok) throw Error(payload.error || 'Ask Masar could not complete the request.');
 return payload;
}

const describe = value => [
 [value.runway, value.sid, value.aircraft].join(' · '),
 `${format.format(value.count)} ${value.count === 1 ? 'departure' : 'departures'}`,
 `${format.format(value.mass_pct)}% of maximum takeoff weight for fuel`,
 `${value.temperature_c === null ? '≈33' : format.format(value.temperature_c)} °C for noise`,
].join(' · ');

function describeStage(event, cases, total) {
 const input = cases[event.case - 1];
 const prefix = input ? `Case ${event.case} of ${total} · ` : '';
 switch (event.stage) {
  case 'preparing': return 'Checking project data and model inputs';
  case 'fuel': return `${prefix}Estimating fuel with OpenAP on ${input.runway} / ${input.sid}`;
  case 'noise': return `${prefix}Modelling noise and resident exposure with Doc 29, ANP and WorldPop`;
  case 'contours': return `${prefix}Building noise contours for the map`;
  case 'reuse': return `${prefix}Reusing fuel and noise for the same aircraft, route, weight and temperature`;
  case 'co2': return `${prefix}Converting OpenAP fuel to CO₂ using ${event.factor} kg CO₂ per kg of fuel`;
  case 'finalizing': return 'Preparing results and source details';
  default: return 'Running the calculation';
 }
}

export function mountAskMasar({panel, getDefaults, focusScenario, showOnMap, restoreMap}) {
 if (!LOCAL.has(location.hostname)) return () => {};
 const assistant = make('section', undefined, 'ask-masar');
 assistant.hidden = true;
 const title = make('h3', 'Ask Masar');
 title.id = 'ask-masar-title';
 assistant.setAttribute('aria-labelledby', title.id);
 const guides = make('div', undefined, 'ask-guides');
 guides.setAttribute('role', 'group');
 guides.setAttribute('aria-label', 'Guided prompts');
 const guidedForm = make('form', undefined, 'ask-guided-form');
 guidedForm.hidden = true;
 const editScenario = make('button', 'Change runway, SID, or aircraft', 'ask-edit-scenario');
 editScenario.type = 'button';
 editScenario.onclick = focusScenario;
 const guidedFields = make('fieldset', undefined, 'ask-fields');
 const review = make('button', 'Review inputs', 'ask-primary');
 review.type = 'submit';
 guidedForm.append(editScenario, guidedFields, review);
 const form = make('form');
 const label = make('label', 'Ask about fuel burn or noise');
 const prompt = make('textarea');
 prompt.rows = 2;
 prompt.maxLength = 1600;
 prompt.required = true;
 prompt.placeholder = 'How much fuel would 20 A320 departures use?';
 label.append(prompt);
 const ask = make('button', 'Interpret question', 'ask-primary');
 ask.type = 'submit';
 const providerNote = make('p', undefined, 'ask-provider');
 providerNote.id = 'ask-masar-provider';
 prompt.setAttribute('aria-describedby', providerNote.id);
 form.append(label, providerNote, ask);
 const status = make('p', undefined, 'ask-status');
 status.setAttribute('role', 'status');
 const elapsed = make('p', undefined, 'ask-elapsed');
 const progress = make('details', undefined, 'ask-progress');
 const timeline = make('ol');
 progress.append(make('summary', 'Calculation steps'), timeline);
 progress.hidden = true;
 const output = make('div', undefined, 'ask-output');
 assistant.append(title, form, guides, guidedForm, status, elapsed, progress, output);
 panel.append(assistant);
 let checked = false;
 let activeGuide = null;
 let activeModel = '';
 let interpretationConfigured = false;

 const guideButtons = guidedPrompts.map(guide => {
  const button = make('button', guide.label);
  button.type = 'button';
  button.setAttribute('aria-pressed', 'false');
  button.onclick = () => {
   if (activeGuide !== guide) openGuide(guide);
   guidedFields.querySelector('input').focus();
  };
  guides.append(button);
  return button;
 });

 function selectGuide(guide) {
  activeGuide = guide;
  guidedForm.hidden = !guide;
  guideButtons.forEach((button, index) => {
   button.setAttribute('aria-pressed', String(guidedPrompts[index] === guide));
  });
 }

 function openGuide(guide) {
  selectGuide(guide);
  guidedFields.replaceChildren(...guide.fields.map(({label, ...attributes}) => {
   const field = make('label', label);
   const input = make('input');
   Object.assign(input, {type: 'number', required: true, ...attributes});
   field.append(input);
   return field;
  }));
  output.replaceChildren();
  clearProgress();
  say('');
 }

 function setBusy(busy) {
  ask.disabled = busy || !interpretationConfigured;
  review.disabled = guidedFields.disabled = editScenario.disabled = busy;
  for (const button of guideButtons) button.disabled = busy;
 }

 const say = (text, error = false) => {
  if (status.textContent !== text) status.textContent = text;
  status.classList.toggle('error', error);
 };
 const fail = error => say(error instanceof SyntaxError || error instanceof TypeError
  ? 'The local server is unavailable. Try reopening the local Map 2 preview.'
  : error.message, true);

 function showElapsed(seconds) {
  const total = Math.floor(seconds);
  const clock = total < 60 ? `${total}s` : `${Math.floor(total / 60)}m ${total % 60}s`;
  elapsed.textContent = `${clock} elapsed`;
 }

 function clearProgress() {
  elapsed.textContent = '';
  progress.hidden = true;
  progress.open = false;
  timeline.replaceChildren();
 }

 function showProgress(state, cases) {
  showElapsed(state.elapsed_s ?? 0);
  const stages = state.progress ?? [];
  if (!stages.length) return '';
  for (const event of stages.slice(timeline.children.length)) {
   timeline.append(make('li', describeStage(event, cases, state.cases_total ?? cases.length)));
  }
  progress.hidden = false;
  const current = timeline.lastElementChild.textContent;
  if (state.status === 'running') say(current);
  return current;
 }

 function showProposal(cases, guided = false) {
  const run = make('button', 'Run calculations', 'ask-primary');
  run.type = 'button';
  output.replaceChildren(
   make('h3', 'Review calculation inputs'),
   ...cases.map((item, i) => make('p', `Case ${i + 1} · ${describe(item)}`)),
   make('p', 'Still air and a standard climb. Mass changes fuel. Temperature changes noise.'),
   run,
  );
  run.onclick = async () => {
   if (guided && Object.entries(getDefaults()).some(([field, value]) => cases[0][field] !== value)) {
    output.replaceChildren();
    say('The scenario selection changed. Review the inputs again.');
    review.focus();
    return;
   }
   setBusy(true);
   run.disabled = true;
   clearProgress();
   showElapsed(0);
   say('Starting the calculation…');
   status.scrollIntoView({block: 'nearest'});
   try {
    let state = await request('/jobs', {cases});
    const id = encodeURIComponent(state.id);
    let stage = showProgress(state, cases);
    while (state.status === 'running') {
     await new Promise(resolve => setTimeout(resolve, 1000));
     state = await request('/jobs/' + id);
     stage = showProgress(state, cases);
    }
    if (state.status !== 'complete') {
     const error = state.error || 'The simulation did not finish.';
     say(stage ? `${error} Last step · ${stage}` : error, true);
     return;
    }
    showResult(state.result);
    say('Calculation complete.');
   } catch (error) {
    fail(error);
   } finally {
    setBusy(false);
    run.disabled = false;
   }
  };
  if (guided) run.focus();
 }

 function showResult(result) {
  const cases = result.cases;
  const table = make('table');
  const head = make('thead');
  const header = make('tr');
  header.append(make('th', 'Modelled result'), ...cases.map((_, i) => make('th', 'Case ' + (i + 1))));
  if (result.difference) header.append(make('th', '2 − 1'));
  for (const item of header.children) item.scope = 'col';
  head.append(header);
  const body = make('tbody');
  const row = (name, values, difference) => {
   const tr = make('tr');
   const th = make('th', name);
   th.scope = 'row';
   tr.append(th, ...values.map(value => make('td', format.format(value))));
   if (result.difference) tr.append(make('td', difference === undefined ? '—' : signed.format(difference)));
   body.append(tr);
  };
  row('Fuel per departure (kg)', cases.map(item => item.scenario.to10k.fuel_kg));
  row('Plan fuel (kg)', cases.map(item => item.plan_fuel_kg), result.difference?.fuel_kg);
  row('Plan CO₂ (kg)', cases.map(item => item.plan_co2_kg), result.difference?.co2_kg);
  for (const threshold of levels) {
   row(`Residents ≥${threshold} dB`, cases.map(item => item.scenario.residents[threshold]), result.difference?.residents[threshold]);
  }
  table.append(head, body);
  const scroll = make('div', undefined, 'ask-table');
  scroll.append(table);
  const buttons = make('div', undefined, 'ask-map-actions');
  cases.forEach((item, i) => {
   const show = make('button', `Show case ${i + 1} on map`);
   show.type = 'button';
   show.onclick = () => showOnMap(item.scenario, item.contour);
   buttons.append(show);
  });
  const restore = make('button', 'Restore manual map');
  restore.type = 'button';
  restore.onclick = restoreMap;
  buttons.append(restore);
  const sources = make('details');
  sources.append(make('summary', 'Models, weather, and source files'));
  sources.append(make('p', `${result.models.fuel} · ${result.models.noise} · ${result.models.population}`));
  cases.forEach(({inputs, scenario, noise_flags}, i) => {
   sources.append(make('p', `Case ${i + 1} · ${format.format(inputs.mass_kg)} kg takeoff mass · ${format.format(inputs.humidity_pct)}% relative humidity · ${format.format(inputs.qnh_hpa)} hPa · ${format.format(inputs.wind_kt)} kt wind · ANP ${scenario.noise_aircraft}`));
   for (const flag of [...scenario.notes, ...scenario.flags, ...noise_flags]) sources.append(make('p', flag));
  });
  const list = make('ul');
  for (const source of result.sources) {
   list.append(make('li', `${source.label} · ${source.path} · SHA-256 ${source.sha256}`));
  }
  sources.append(list);
  output.replaceChildren(
   make('h3', 'Fuel and noise estimates'),
   ...cases.map((item, i) => make('p', `Case ${i + 1} · ${describe(item.inputs)}`)),
   scroll,
   make('p', 'Fuel covers brake release to 10,000 ft. Plan fuel uses the unrounded per-departure estimate × departure count. Noise and residents describe one departure’s peak footprint.'),
   buttons, sources,
  );
 }

 function showClarification(message, question, defaults, clarifications) {
  if (clarifications.length === 3) {
   output.append(make('p', 'Add the missing details to your original question and try again.'));
   prompt.focus();
   return;
  }
  const followUp = make('form');
  const label = make('label', 'Answer the follow-up');
  const reply = make('input');
  reply.required = true;
  reply.maxLength = 400;
  label.append(reply);
  const send = make('button', 'Continue');
  send.type = 'submit';
  followUp.append(label, send);
  output.append(followUp);
  followUp.onsubmit = event => {
   event.preventDefault();
   if (!reply.value.trim() || ask.disabled) return;
   send.disabled = true;
   askQuestion(question, defaults, [...clarifications, {prompt: message, reply: reply.value.trim()}])
    .finally(() => {send.disabled = false;});
  };
  reply.focus();
 }

 async function askQuestion(question, defaults, clarifications = []) {
  if (ask.disabled) return;
  setBusy(true);
  clearProgress();
  say(`Interpreting your question with ${activeModel}…`);
  const start = performance.now();
  showElapsed(0);
  status.scrollIntoView({block: 'nearest'});
  const timer = setInterval(() => showElapsed((performance.now() - start) / 1000), 1000);
  try {
   const answer = await request('/interpret', {question, defaults, clarifications});
   output.replaceChildren();
   if (answer.kind === 'calculate') {
    showProposal(answer.cases);
    say('Review the inputs, then run the calculation.');
   } else {
    say(answer.message);
    if (answer.kind === 'clarify') showClarification(answer.message, question, defaults, clarifications);
   }
  } catch (error) {
   fail(error);
  } finally {
   clearInterval(timer);
   elapsed.textContent = '';
   setBusy(false);
  }
 }

 form.onsubmit = event => {
  event.preventDefault();
  if (ask.disabled) return;
  selectGuide(null);
  output.replaceChildren();
  askQuestion(prompt.value, getDefaults());
 };

 guidedForm.oninput = () => {
  output.replaceChildren();
  clearProgress();
  say('');
 };

 guidedForm.onsubmit = async event => {
  event.preventDefault();
  if (review.disabled || !activeGuide) return;
  const defaults = getDefaults();
  if (!defaults.sid) {
   say('Select a published SID before reviewing the inputs.', true);
   return;
  }
  const values = Object.fromEntries(Array.from(guidedFields.elements, input => [input.name, input.valueAsNumber]));
  const cases = activeGuide.cases(values).map(item => ({...defaults, ...item}));
  setBusy(true);
  output.replaceChildren();
  clearProgress();
  say('Checking calculation inputs…');
  try {
   const validated = await request('/validate', {cases});
   showProposal(validated.cases, true);
   say('Review the inputs, then run the calculation.');
  } catch (error) {
   fail(error);
  } finally {
   setBusy(false);
  }
 };

 return () => {
  if (checked) return;
  checked = true;
  request('/status').then(({enabled, model, provider, configured, message}) => {
   const modelLabel = modelLabels[model] ?? model ?? 'the configured model';
   activeModel = provider === 'groq' ? `${modelLabel} on Groq` : modelLabel;
   interpretationConfigured = configured !== false;
   prompt.disabled = ask.disabled = !interpretationConfigured;
   providerNote.textContent = message ?? (provider === 'groq'
    ? 'Your question and scenario selections are sent to Groq.'
    : 'Free-text questions use local Ollama.');
   assistant.hidden = !enabled;
  }).catch(() => {checked = false;});
 };
}
