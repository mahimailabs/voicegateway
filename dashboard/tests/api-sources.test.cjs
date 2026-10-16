const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { parse } = require('yaml');
const contract = path.resolve(__dirname, '../../docs/api/openapi.yaml');
const checker = import('../scripts/check-sources');
const load = () => parse(fs.readFileSync(contract, 'utf8'));

test('source checker accepts the contract and checks nested properties', async () => {
  const { checkSpec } = await checker;
  const result = checkSpec(load());
  assert.deepEqual(result.errors, []);
  assert.ok(result.properties > 0);
});

test('source checker rejects missing attribution inside nested arrays', async () => {
  const { checkSpec } = await checker;
  const spec = load();
  const schema = spec.components.schemas;
  delete schema.Workers.properties.workers.items.properties.samples.items.properties.load['x-source'];
  assert.match(checkSpec(spec).errors.join('\n'), /samples.*load.*missing structured x-source/);
});

test('source checker rejects invented evidence and unresolved references', async () => {
  const { checkSpec } = await checker;
  const spec = load();
  spec.components.schemas.Call.properties.room['x-source'].pointer = '/no-such-captured-field';
  spec.components.schemas.Call.properties.turns.items.$ref = '#/components/schemas/Invented';
  const errors = checkSpec(spec).errors.join('\n');
  assert.match(errors, /source JSON pointer does not resolve/);
  assert.match(errors, /unresolved schema reference/);
});

test('source checker rejects zero defaults, non-nullable metrics and missing units', async () => {
  const { checkSpec } = await checker;
  const spec = load();
  const metric = spec.components.schemas.Turn.properties.ttsMs;
  metric.type = 'number';
  metric.default = 0;
  delete metric['x-unit'];
  const errors = checkSpec(spec).errors.join('\n');
  assert.match(errors, /measurement must allow null/);
  assert.match(errors, /cannot default to zero/);
  assert.match(errors, /lacks a unit/);
});

test('source checker rejects a public read and missing upstream-failure response', async () => {
  const { checkSpec } = await checker;
  const spec = load();
  const operation = Object.values(spec.paths)[0].get;
  delete operation['x-access'];
  delete operation.responses['503'];
  const errors = checkSpec(spec).errors.join('\n');
  assert.match(errors, /mandatory authorisation/);
  assert.match(errors, /missing 503/);
});

test('read inventory covers actual sources, routes and widget ids', async () => {
  const { checkCoverage } = await checker;
  assert.deepEqual(checkCoverage(load()).errors, []);
});

test('read inventory rejects an omitted reader and a removed endpoint', async () => {
  const { checkCoverage } = await checker;
  const spec = load();
  const root = path.resolve(__dirname, '../..');
  const inventory = JSON.parse(fs.readFileSync(path.join(root, 'docs/api/readers.json'), 'utf8'));
  const omitted = inventory.pop();
  const sources = checkCoverage(spec, root, inventory).errors.join('\n');
  assert.ok(sources.includes(omitted.file));
  delete spec.paths['/api/v1/org/{orgId}/project/{projectId}/calls'];
  assert.match(checkCoverage(spec).errors.join('\n'), /unknown operation listCalls/);
});

test('route coverage rejects new or stale page and widget entries', async () => {
  const { checkCoverage } = await checker;
  const root = path.resolve(__dirname, '../..');
  const surfaces = JSON.parse(fs.readFileSync(path.join(root, 'docs/api/surfaces.json'), 'utf8'));
  delete surfaces.routes['/calls'];
  surfaces.routes['/retired-page'] = ['listCalls'];
  surfaces.widgets.push('unimplemented-widget');
  const errors = checkCoverage(load(), root, undefined, surfaces).errors.join('\n');
  assert.match(errors, /calls: missing page-to-endpoint/);
  assert.match(errors, /retired-page: stale route/);
  assert.match(errors, /widget inventory drift/);
});

test('source checker rejects directories and contradictory captured discriminators', async () => {
  const { checkSpec } = await checker;
  const spec = load();
  spec.components.schemas.Call.properties.id['x-source'].file = 'docs/api';
  spec.components.schemas.TranscriptEvent.properties.type.const = 'invented_event';
  const errors = checkSpec(spec).errors.join('\n');
  assert.match(errors, /source evidence does not exist: docs\/api/);
  assert.match(errors, /constant contradicts its captured evidence/);
});

test('source checker rejects a project endpoint with an organisation-only access rule', async () => {
  const { checkSpec } = await checker;
  const spec = load();
  const access = spec.paths['/api/v1/org/{orgId}/project/{projectId}/calls'].get['x-access'];
  access.scope = 'organisation';
  delete access.projectIsolation;
  access.minimumRole = 'unrestricted';
  const errors = checkSpec(spec).errors.join('\n');
  assert.match(errors, /scope does not match/);
  assert.match(errors, /project isolation is mandatory/);
  assert.match(errors, /supported organisation role/);
});
