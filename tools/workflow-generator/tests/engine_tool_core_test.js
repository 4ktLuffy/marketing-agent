// 64 "Prepare" as it ships: on a core install (ENGINE_URL empty, no content engine) the chat tool
// answers that the engine is not installed instead of calling a missing service.
// Run: node tests/engine_tool_core_test.js   (after build.py)
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../../..');
const wf = JSON.parse(fs.readFileSync(path.join(ROOT, '64-wf-tool-content-engine', 'workflow.json'), 'utf8'));
const prepare = wf.nodes.find(n => n.name === 'Prepare');
assert.ok(prepare, 'no Prepare node in 64');
const run = (input, env) => new Function('$input', '$env', prepare.parameters.jsCode)(
  { first: () => ({ json: input }), all: () => [{ json: input }] }, env);

const ask = { topic: 'Decaf launch', channels: 'linkedin, x', month: '2026-11' };

// core: no engine -> ok:false with a reason that names the fix
const core = run(ask, { ENGINE_URL: '' })[0].json;
assert.equal(core.ok, false);
assert.match(core.result, /not installed/);
assert.match(core.result, /--profile growth/);
assert.equal(run(ask, {})[0].json.ok, false);

// negative control: with the engine set, the same request is accepted
const growth = run(ask, { ENGINE_URL: 'http://content-engine:8000' })[0].json;
assert.equal(growth.ok, true, JSON.stringify(growth));
console.log('ok: 64 skips cleanly without the content engine, plans with it');
