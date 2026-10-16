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


test('relative positions require clock bindings while durations remain intervals', async () => {
  const { checkSpec } = await checker;
  const spec = load();
  delete spec.components.schemas.OverlapEvent.properties.detectedAt['x-clock'];
  spec.components.schemas.Span.required = spec.components.schemas.Span.required.filter((key) => key !== 'clockId');
  const errors = checkSpec(spec).errors.join('\n');
  assert.match(errors, /detectedAt.*resolvable clock reference/);
  assert.match(errors, /startMs.*clock reference must be required/);
  assert.equal(spec.components.schemas.OverlapEvent.properties.detectionDelay['x-unit'], 'seconds');
});

test('clock schema preserves unknown anchors and rejects fabricated alignment', () => {
  const Ajv = require('ajv/dist/2020');
  const validate = new Ajv({ strict: false, validateFormats: false }).compile(load().components.schemas.Clock);
  const unknown = { id: 'events', origin: 'first_event', originAt: null, offsetToCallStartSeconds: null, alignment: 'unknown' };
  assert.equal(validate(unknown), true);
  assert.equal(validate({ ...unknown, offsetToCallStartSeconds: 0 }), false);
  assert.equal(validate({ ...unknown, alignment: 'observed' }), false);
  assert.equal(validate({ ...unknown, alignment: 'inferred' }), false);
  assert.equal(validate({ ...unknown, alignment: 'observed', offsetToCallStartSeconds: 0 }), true);
  assert.equal(validate({ ...unknown, alignment: 'inferred', offsetToCallStartSeconds: -0.25 }), true);
  for (const example of load().components.schemas.Clock.examples) assert.equal(validate(example), true);
  const incomplete = { ...unknown };
  delete incomplete.origin;
  assert.equal(validate(incomplete), false);
});

test('a position bound to another record\'s clock is rejected', async () => {
  const { checkSpec } = await checker;
  const spec = load();
  spec.components.schemas.Span.properties.startMs['x-clock'] = '#/components/schemas/Turn/properties/clockId';
  assert.match(checkSpec(spec).errors.join('\n'), /Span\/properties\/startMs: clock reference must name its own record's clockId/);
  assert.deepEqual(checkSpec(load()).errors, [], 'a recording segment on its recording clock stays valid');
});

test('the calls list returns summaries filtered and sorted on the server', () => {
  const spec = load();
  const list = spec.paths['/api/v1/org/{orgId}/project/{projectId}/calls'].get;
  const page = spec.components.schemas.CallPage.properties.items.items.$ref;
  assert.equal(page, '#/components/schemas/CallSummary');
  for (const heavy of ['spans', 'turns', 'events', 'clocks']) assert.equal(spec.components.schemas.CallSummary.properties[heavy], undefined, heavy);
  const params = new Set(list.parameters.map((p) => p.name).filter(Boolean));
  for (const name of ['channel', 'issues', 'answerMsMin', 'answerMsMax', 'durationSecondsMin', 'durationSecondsMax', 'turnsMin', 'turnsMax', 'unknown', 'tag', 'recorded', 'archived', 'sort', 'order']) assert.ok(params.has(name), name);
  const sort = list.parameters.find((p) => p.name === 'sort').schema.enum;
  assert.deepEqual(sort, ['startedAt', 'durationSeconds', 'turnCount', 'answerMs', 'issues']);
  assert.ok(list.parameters.find((p) => p.name === 'unknown').schema.items.enum.includes('answerMs'), 'unknown stays filterable without matching zero');
});

test('page aggregates are server reads over a required range', () => {
  const spec = load();
  const root = path.resolve(__dirname, '../..');
  const surfaces = JSON.parse(fs.readFileSync(path.join(root, 'docs/api/surfaces.json'), 'utf8'));
  const reads = { getCallStats: '/overview', listIssues: '/issues', listAlertEvents: '/alerts' };
  for (const [id, route] of Object.entries(reads)) {
    const op = Object.values(spec.paths).map((p) => p.get).find((g) => g?.operationId === id);
    assert.ok(op, id);
    for (const bound of ['from', 'to']) assert.equal(op.parameters.find((p) => p.name === bound)?.required, true, `${id} ${bound}`);
    assert.ok(surfaces.routes[route].includes(id), `${route} reads ${id}`);
  }
  assert.ok(!surfaces.routes['/issues'].includes('listCalls'), 'Issues no longer totals call pages');
  assert.ok(!surfaces.routes['/alerts'].includes('listCalls'), 'Alerts no longer totals call pages');
});

test('embed tokens reach only the reads their widgets need', async () => {
  const { checkSpec, checkCoverage } = await checker;
  const spec = load();
  const settings = spec.paths['/api/v1/org/{orgId}/project/{projectId}/settings'].get;
  settings['x-embed-access'] = { ...spec.paths['/api/v1/org/{orgId}/project/{projectId}/calls'].get['x-embed-access'] };
  assert.match(checkCoverage(spec).errors.join('\n'), /calls getProjectSettings: embed access beyond/);
  const calls = spec.paths['/api/v1/org/{orgId}/project/{projectId}/calls'].get;
  delete calls['x-embed-access'];
  assert.match(checkCoverage(spec).errors.join('\n'), /calls listCalls: widget read lacks embed access/);
  const call = spec.paths['/api/v1/org/{orgId}/project/{projectId}/calls/{callId}'].get['x-embed-access'];
  delete call.originAllowList;
  call.credential = 'project_key';
  const errors = checkSpec(spec).errors.join('\n');
  assert.match(errors, /origin allow-list/);
  assert.match(errors, /must use an embed token/);
});
