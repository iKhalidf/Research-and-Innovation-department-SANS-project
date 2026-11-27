import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {forecastImpact, loadReplay, validateReplay} from '../map-2/forecast-layer.mjs';

const synthetic = JSON.parse(readFileSync(new URL('../map-2/forecast-example.json', import.meta.url)));
assert.equal(synthetic.source.kind, 'synthetic');
assert.equal(validateReplay(synthetic), synthetic);

const staleBaseline = structuredClone(synthetic);
staleBaseline.hours[0].baseline += 1;
assert.throws(() => validateReplay(staleBaseline), /invalid hourly record/);

const wrongScores = structuredClone(synthetic);
wrongScores.scores.model.mae += 1;
assert.throws(() => validateReplay(wrongScores), /invalid test scores/);


const local = structuredClone(synthetic);
local.source.kind = 'a2';
const localRequests = [];
const replay = await loadReplay({
 allowLocal: true,
 fetcher: async path => {
  localRequests.push(String(path));
  return {ok: true, json: async () => local};
 },
});
assert.equal(replay.source.kind, 'a2');
assert.deepEqual(localRequests, ['/forecast-data/replay.json']);

const fallback = [];
assert.equal((await loadReplay({
 allowLocal: true,
 fetcher: async path => {
  fallback.push(String(path));
  return fallback.length === 1
   ? {ok: false, status: 404}
   : {ok: true, json: async () => synthetic};
 },
})).source.kind, 'synthetic');
assert.equal(fallback[0], '/forecast-data/replay.json');
assert.match(fallback[1], /\/forecast-example\.json$/);

const publicRequests = [];
await assert.rejects(loadReplay({
 allowLocal: false,
 fetcher: async path => {
  publicRequests.push(String(path));
  return {ok: true, json: async () => local};
 },
}), /public forecast example must use synthetic/);
assert.equal(publicRequests.length, 1);
assert.match(publicRequests[0], /\/forecast-example\.json$/);

const scenario = {to10k: {fuel_kg: 125, co2_kg: 397.5}, residents: {70: 31}};
assert.deepEqual(forecastImpact(3, scenario), {fuelKg: 375, co2Kg: 1192.5, residents70: 31});
assert.deepEqual(forecastImpact(0, scenario), {fuelKg: 0, co2Kg: 0, residents70: 0});

console.log('Forecast artifact, source selection and impact checks passed.');
