// Checks the approval form's video script parser (workflows.VIDEO_SCRIPT_PARSE) as it ships:
// the parser is read from 38's built workflow.json, and the scripts are formatted by the code
// of 57 ("For the gate" + "Assemble") and 65 ("Draft text"), also read from workflow.json.
// Run: node tests/video_script_parse_test.js   (after build.py)
// Negative control: replace the parser with one that ignores the edited text (returns the old
// item's script) and the "reviewer edits" tests fail; see the end of this file.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../../..');
const nodeCode = (folder, name) => {
  const wf = JSON.parse(fs.readFileSync(path.join(ROOT, folder, 'workflow.json'), 'utf8'));
  const n = wf.nodes.find(x => x.name === name);
  assert.ok(n, `${folder}: no node "${name}"`);
  return n.parameters.jsCode;
};
// Run a Code node with $input and $('Node') mocked.
const runNode = (code, input, nodes, env = {}) => new Function('$input', '$', '$env', code)(
  { first: () => ({ json: input[0] }), all: () => input.map(json => ({ json })) },
  n => { assert.ok(n in nodes, `node ${n} not mocked`); return { first: () => ({ json: nodes[n] }), all: () => [{ json: nodes[n] }] }; },
  env);

const dec = nodeCode('38-wf-approval-form', 'Decisions');
const cutAt = dec.indexOf('// A field sent twice');
assert.ok(cutAt > 0, 'Decisions does not start with the parser');
let parse = new Function(dec.slice(0, cutAt) + '\nreturn parseVideoScript;')();

const SCRIPT = {
  hook: { spoken: 'Ever wondered how decaf loses its caffeine?', on_screen: 'Decaf, explained' },
  beats: [
    { spoken: 'It starts as green beans, soaked in warm water.', on_screen: 'Green beans first', shot: 'close-up of green beans in water' },
    { spoken: 'The water carries the caffeine out, gently.', on_screen: 'Water does the work', shot: 'slow pour over a glass tank' },
    { spoken: 'Then the beans dry and go to the roaster.', on_screen: 'Dry, then roast', shot: 'roaster drum turning' },
  ],
  cta: 'Follow for more coffee tips',
  caption: 'How decaf is made, in 30 seconds.',
  hashtags: ['decaf', 'coffee', 'cold brew'],
  estimated_seconds: 35,
};
// What the parser gives back: no estimated_seconds, hashtags as 57 writes them (no spaces).
const expected = s => ({ hook: s.hook, beats: s.beats.map(b => ({ ...b, shot: b.shot ?? null })), cta: s.cta,
  caption: s.caption, hashtags: s.hashtags.map(h => h.replace(/^#/, '').replace(/\s+/g, '')) });

// 57: the gate text, then the body with "Estimated length" and the shot list.
function format57(out) {
  const gateItems = runNode(nodeCode('57-wf-tool-content-formats', 'For the gate'), [{ output: out }],
    { Prepare: { format: 'video_script', topic: 'decaf' }, 'Build vars': { context: '' } });
  const text = gateItems[0].json.text;
  const a = runNode(nodeCode('57-wf-tool-content-formats', 'Assemble'), [{ ref: '0', text, problems: [], warnings: [] }],
    { Prepare: { format: 'video_script', topic: 'decaf' }, Write: { output: out }, 'Build vars': {} });
  return a[0].json.body;
}
// 65: prefix + text + suffix, as "After gate" saves it.
function format65(out) {
  const r = runNode(nodeCode('65-wf-sched-engine-drafter', 'Draft text'), [{ output: out }],
    { Request: { format: 'video_script', channel: 'video' } });
  const j = r[0].json;
  assert.equal(j.error, '');
  return j.prefix + j.text + j.suffix;
}

let passed = 0;
const edits = [];   // the reviewer-edit tests, run again in the negative control
const t = (name, fn, isEdit = false) => { fn(); passed++; console.log(`ok - ${name}`); if (isEdit) edits.push([name, fn]); };
const ok = (text, old) => { const p = parse(text, old); assert.ok(p.script, `parse error: ${p.error}\n${text}`); return p.script; };

const body57 = format57(SCRIPT);
const body65 = format65(SCRIPT);

t('57 text has the shape the parser expects', () => {
  assert.ok(body57.startsWith('Hook: Ever wondered'), body57);
  assert.ok(/\nEstimated length: 35 s\nShot list:\n- hook: \(film what the hook says\)\n- beat 1: close-up/.test(body57), body57);
});

t('round trip 57: format -> parse -> same JSON (shots from the shot list)', () => {
  assert.deepEqual(ok(body57, body57), expected(SCRIPT));
});

t('round trip 65: format -> parse -> same JSON', () => {
  assert.deepEqual(ok(body65, body65), expected(SCRIPT));
});

t('round trip without an old body: same JSON, shots null', () => {
  const e = expected(SCRIPT);
  assert.deepEqual(ok(body57, null), { ...e, beats: e.beats.map(b => ({ ...b, shot: null })) });
});

t('the "Estimated length"/"Shot list" tail is ignored, even when edited', () => {
  const edited = body57.replace('Estimated length: 35 s', 'Estimated length: 90 s').replace('- beat 1: close-up', '- beat 1: Say: nope');
  assert.deepEqual(ok(edited, body57), expected(SCRIPT));
  const noTail = body57.split('\n\nEstimated length')[0];
  assert.deepEqual(ok(noTail, body57), expected(SCRIPT));
});

t('edit: a changed spoken line and on-screen text', () => {
  const edited = body57.replace('The water carries the caffeine out, gently.', 'Water pulls the caffeine out, no chemicals.')
    .replace('On screen: Dry, then roast', 'On screen: Dried and roasted');
  const s = ok(edited, body57);
  assert.equal(s.beats[1].spoken, 'Water pulls the caffeine out, no chemicals.');
  assert.equal(s.beats[1].shot, 'slow pour over a glass tank');       // same beat, same shot (by index)
  assert.equal(s.beats[2].on_screen, 'Dried and roasted');
  assert.equal(s.beats[2].shot, 'roaster drum turning');
  assert.deepEqual(s.hook, SCRIPT.hook);
}, true);

t('edit: a beat removed (shots follow their beats)', () => {
  const edited = body57.replace('Say: The water carries the caffeine out, gently.\nOn screen: Water does the work\n\n', '');
  const s = ok(edited, body57);
  assert.equal(s.beats.length, 2);
  assert.deepEqual(s.beats.map(b => b.on_screen), ['Green beans first', 'Dry, then roast']);
  assert.deepEqual(s.beats.map(b => b.shot), ['close-up of green beans in water', 'roaster drum turning']);
}, true);

t('edit: a beat added (it has no shot)', () => {
  const edited = body57.replace('Close: ', 'Say: Same taste, none of the jitters.\nOn screen: All taste, no jitters\n\nClose: ');
  const s = ok(edited, body57);
  assert.equal(s.beats.length, 4);
  assert.deepEqual(s.beats[3], { spoken: 'Same taste, none of the jitters.', on_screen: 'All taste, no jitters', shot: null });
  assert.equal(s.beats[0].shot, 'close-up of green beans in water');
  assert.equal(s.cta, SCRIPT.cta);
}, true);

t('edit: labels removed partially (Hook:, one Say:, one On screen:, Close:)', () => {
  const edited = body57.replace('Hook: ', '').replace('Say: It starts', 'It starts')
    .replace('On screen: Water does the work', 'Water does the work').replace('Close: ', '');
  assert.deepEqual(ok(edited, body57), expected(SCRIPT));
});

t('edit: bold labels, CRLF, extra blank lines, lower case', () => {
  const edited = body57.replace('Hook:', '**Hook:**').replace(/On screen:/g, 'on screen:').replace(/\n/g, '\r\n').replace('\r\n\r\nClose', '\r\n\r\n\r\nClose');
  assert.deepEqual(ok(edited, body57), expected(SCRIPT));
});

t('edit: caption and hashtags changed', () => {
  const edited = body57.replace('Caption: How decaf is made, in 30 seconds.', 'Caption: Decaf, the gentle way.').replace('#decaf #coffee #coldbrew', '#decaf #swisswater');
  const s = ok(edited, body57);
  assert.equal(s.caption, 'Decaf, the gentle way.');
  assert.deepEqual(s.hashtags, ['decaf', 'swisswater']);
}, true);

t('garbage and broken scripts are errors with a reason', () => {
  const bad = [
    ['', /no hook/],
    ['lol just post it', /no beats|On screen/],
    ['Hook: hi\nOn screen: hi\n\nClose: bye', /no beats/],
    ['Hook: hi\n\nSay: a\nOn screen: b\n\nClose: bye', /hook has no "On screen:"/],
    [body57.replace('On screen: Green beans first', ''), /On screen/],
    [body57.replace('Close: Follow for more coffee tips\n', '').replace('Caption:', 'Caption:'), /Close|On screen/],
    [body57.replace('Close: ', 'Close: a\n\nClose: '), /two "Close:"/],
    [body57 + '\nSay: late\nOn screen: late', null],   // after the tail: ignored, still fine
  ];
  for (const [text, re] of bad) {
    const p = parse(text, body57);
    if (re === null) { assert.ok(p.script, p.error); continue; }
    assert.ok(p.error && !p.script, `expected an error for:\n${text}\ngot ${JSON.stringify(p)}`);
    assert.match(p.error, re);
  }
  const nine = 'Hook: h\nOn screen: h\n\n' + Array.from({ length: 9 }, (_, i) => `Say: s${i}\nOn screen: o${i}\n`).join('\n') + '\nClose: c';
  assert.match(parse(nine, null).error, /9 beats/);
});

// Negative control: a parser that ignores the edit (it returns the old item's script) must
// fail every reviewer-edit test above.
t('negative control: every edit test fails on an edit-ignoring parser', () => {
  const real = parse;
  parse = (text, old) => real(old, old);
  try {
    for (const [name, fn] of edits) assert.throws(fn, assert.AssertionError, `"${name}" passed with an edit-ignoring parser`);
  } finally { parse = real; }
  assert.equal(edits.length, 4);
});

console.log(`${passed} passed`);
