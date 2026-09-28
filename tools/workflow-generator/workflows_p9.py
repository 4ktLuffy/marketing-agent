"""Phase 9 workflows (77): clips from a long video, as a chat tool.

73-clip-finder cuts the clips (transcribe, score, render); this workflow waits for its job,
writes a caption per clip from what the speaker says in it, runs the quality gate and saves
each clip to the calendar as a `video` item for approval. Contract: 73-clip-finder/README.md.
"""
from n8nlib import Workflow, _uid, call_workflow, code, gateway, http, if_true, loop_one_by_one, sub_trigger

FORM_URL = "${String($env.N8N_PUBLIC_URL || 'http://localhost:5678/').trim().replace(/\\/?$/, '/')}"
POLL_S = 20        # < 65 s: n8n keeps the execution in memory instead of parking it in the database
BUSY_RETRY_S = 30  # 73 runs one job at a time (429 while another runs)

# Pure: tests can run it. Channels the caption may be written for; the platform rules (14)
# know x, linkedin, instagram, facebook, threads, mastodon: others are checked as instagram.
CLIPS_PREPARE = r"""
const s = $input.first().json;
const url = String(s.url || '').trim();
const ALIAS = { reels: 'instagram', reel: 'instagram', ig: 'instagram', insta: 'instagram', 'instagram reels': 'instagram',
  'tik tok': 'tiktok', shorts: 'youtube', 'youtube shorts': 'youtube', yt: 'youtube', fb: 'facebook', twitter: 'x' };
const KNOWN = ['instagram', 'tiktok', 'youtube', 'facebook', 'x', 'linkedin', 'threads'];
const RULES = ['x', 'linkedin', 'instagram', 'facebook', 'threads', 'mastodon'];
const asked = String(s.channels || '').toLowerCase().split(/\s*(?:,|;|\/|\band\b)\s*/).map(c => c.trim()).filter(Boolean)
  .map(c => ALIAS[c] || c);
const channels = [...new Set(asked.filter(c => KNOWN.includes(c)))];
const dropped = asked.filter(c => !KNOWN.includes(c));
if (!channels.length) channels.push('instagram', 'tiktok');
const cap = Math.max(1, Math.min(10, Math.floor(Number($env.CLIPS_MAX_PER_VIDEO || 5)) || 5));
const n = parseInt(String(s.max_clips || '').replace(/[^\d]/g, ''), 10);
const max_clips = Number.isFinite(n) && n > 0 ? Math.min(n, cap) : Math.min(3, cap);
const waitMin = Math.max(1, Number($env.CLIPS_MAX_WAIT_MIN || 30) || 30);
if (!$env.CLIPS_URL) return [{ json: { ok: false, result:
  'The clip finder (73-clip-finder) is not set up here: CLIPS_URL is empty on the n8n container.' } }];
if (!/^https?:\/\/[^\s/]+\/\S*$/i.test(url)) return [{ json: { ok: false, result:
  'Send me a direct link to the video or audio file (http or https, e.g. https://cdn.example.com/webinar.mp4) and I will cut it into short clips.' } }];
return [{ json: { ok: true, url, max_clips, channels, caption_channel: channels[0],
  gate_channel: RULES.includes(channels[0]) ? channels[0] : 'instagram', dropped,
  topic: String(s.topic || '').trim().slice(0, 1000), started: Date.now(), deadline: Date.now() + waitMin * 60000, wait_min: waitMin } }];
"""

# What a service said, as one line (FastAPI: detail is a string or a list of errors).
DETAIL_JS = r"""
const detailOf = r => {
  const b = r && r.body;
  const d = b && typeof b === 'object' ? b.detail : b;
  const e = r && r.error;
  const s = typeof d === 'string' ? d
    : Array.isArray(d) ? d.map(x => [].concat((x && x.loc) || []).slice(1).join('.') + ' ' + ((x && x.msg) || '')).join('; ')
    : d ? JSON.stringify(d) : e ? (typeof e === 'string' ? e : e.message || JSON.stringify(e)) : 'no answer';
  return String(s).replace(/\s+/g, ' ').trim().slice(0, 400);
};
"""

CLIPS_SUBMITTED = DETAIL_JS + r"""
const p = $('Prepare').first().json;
const r = $input.first().json || {};
if (r.statusCode === 202 && r.body && r.body.id) return [{ json: { state: 'wait', id: String(r.body.id) } }];
if (r.statusCode === 429 && Date.now() < p.deadline) return [{ json: { state: 'busy' } }];
const why = detailOf(r);
let result;
if (r.statusCode === 422) {
  // 73 refuses platform links (YouTube, TikTok, …): their terms forbid downloading.
  result = `I can't cut clips from that link. The clip finder says: "${why}".\n` +
    'Download the video yourself (if you have the rights), put the file somewhere you control ' +
    '(your storage bucket, CDN or podcast host) and send me the direct link to the file, e.g. https://…/webinar.mp4.';
} else if (r.statusCode === 429) {
  result = `The clip finder was busy with another video for ${p.wait_min} minutes; try again later.`;
} else if (r.statusCode === 413) {
  result = `That file is too large for the clip finder: ${why}.`;
} else {
  result = `The clip finder could not take the video (${r.statusCode ? 'HTTP ' + r.statusCode + ': ' : ''}${why}).`;
}
return [{ json: { state: 'error', result } }];
"""

# 73's job: queued|running|done|failed. Network errors and 5xx are waited out until the deadline.
CLIPS_JOB_STATE = DETAIL_JS + r"""
const p = $('Prepare').first().json;
const id = $('Wait for clips').first().json.id;
const r = $input.first().json || {};
const b = r.body && typeof r.body === 'object' ? r.body : {};
if (r.statusCode === 200 && b.status === 'done') {
  const clips = (b.clips || []).filter(c => c && /^https?:\/\/\S+$/.test(c.url || ''));
  if (clips.length) return [{ json: { state: 'done', id, job: b, clips } }];
  return [{ json: { state: 'failed', result: `The clip finder went through the video but found no clip worth posting` +
    `${(b.notes || []).length ? ' (' + b.notes.join('; ').slice(0, 300) + ')' : ''}.` } }];
}
if (r.statusCode === 200 && b.status === 'failed')
  return [{ json: { state: 'failed', result: `The clip finder could not cut clips from ${p.url}: ${String(b.error || 'no reason given').slice(0, 400)}` } }];
if (r.statusCode === 404 || r.statusCode === 401)
  return [{ json: { state: 'failed', result: `The clip finder lost job ${id} (HTTP ${r.statusCode}: ${detailOf(r)}).` } }];
if (Date.now() > p.deadline)
  return [{ json: { state: 'failed', result: `The clips are not ready after ${p.wait_min} minutes (job ${id}, stage ` +
    `${b.stage || 'unknown'}). The clip finder keeps working on it, but nothing was saved to the calendar: ask again later with a shorter video, or raise CLIPS_MAX_WAIT_MIN.` } }];
return [{ json: { state: 'wait', id, stage: b.stage || null, progress: b.progress ?? null, last: r.statusCode || detailOf(r) } }];
"""

CLIPS_EACH = r"""
const d = $input.first().json;
const source = String((d.job.source || {}).name || $('Prepare').first().json.url).slice(0, 300);
return d.clips.map(c => ({ json: { job_id: d.id, source, index: c.index, title: String(c.title || '').trim(),
  start_s: Number(c.start_s), end_s: Number(c.end_s), duration_s: Number(c.duration_s), score: c.score,
  transcript: String(c.transcript || '').trim(), url: c.url, poster_url: /^https?:\/\/\S+$/.test(c.poster_url || '') ? c.poster_url : null } }));
"""

# The transcript is the only source: the caption may say what the speaker says, nothing more
# (the gateway adds the approved facts itself, the claim checker in 35 gets the transcript).
CLIPS_CAPTION_VARS = r"""
const c = $('Loop clips').first(1).json;   // output 1 = the current clip
const p = $('Prepare').first().json;
const cut = (s, n) => s.length <= n ? s : s.slice(0, n).replace(/\s+\S*$/, '') + '…';
const topic = `${c.title || 'A clip from our video'}: ${cut(c.transcript, 400)}`;
const context = [`What the speaker says in this clip (our own video; state nothing it does not say):\n${c.transcript}`,
  p.topic ? `About the video: ${p.topic}` : ''].filter(Boolean).join('\n\n');
return [{ json: { vars: { topic, channels: p.caption_channel, context } } }];
"""

CLIPS_FOR_GATE = DETAIL_JS + r"""
const c = $('Loop clips').first(1).json;
const p = $('Prepare').first().json;
const res = $input.first().json || {};
const posts = ((res.output || {}).posts) || [];
const post = posts.find(x => String(x.channel || '').trim().toLowerCase() === p.caption_channel) || posts[0];
const text = post ? String(post.text || '').trim() : '';
const context = [c.transcript, p.topic].filter(Boolean).join('\n');
if (!text) return [{ json: { ok: false, error: `no caption from the model (${res.output ? 'empty' : detailOf(res)})` } }];
return [{ json: { ok: true, text, channel: p.gate_channel, context, ref: String(c.index),
  hook_style: post.hook_style || null } }];
"""

CLIPS_ASSEMBLE = r"""
const c = $('Loop clips').first(1).json;
const p = $('Prepare').first().json;
const f = $('For the gate').first().json;
const g = f.ok ? ($input.first().json || {}) : {};
const problems = !f.ok ? [f.error] : Array.isArray(g.problems) ? g.problems : ['the quality gate gave no result'];
const warnings = f.ok && Array.isArray(g.warnings) ? [...new Set(g.warnings)] : [];
const caption = f.ok ? String(g.text || f.text) : '[write a caption for this clip]';
const t = s => { s = Math.max(0, Math.round(s)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const range = `${t(c.start_s)}–${t(c.end_s)}`;
const secs = Math.round(c.duration_s * 10) / 10;
// The clip's range and source go in the notes: the body is what gets published.
const notes = [`clip ${range} from ${c.source} (score ${c.score})`, `video preview: ${c.url} (${secs} s)`,
  `clip ${c.index} of job ${c.job_id}, caption for ${p.channels.join(', ')}`,
  problems.length ? `Quality gate: ${problems.join('; ')}` : '', warnings.length ? `warnings: ${warnings.join('; ')}` : '']
  .filter(Boolean).join(' | ');
return [{ json: { title: (c.title || `Clip ${c.index}`).slice(0, 120), channel: 'video',
  body: caption, status: problems.length ? 'draft' : 'in_review', notes,
  video_url: c.url, image_url: c.poster_url, hook_style: f.hook_style || null,
  range, secs, problems, index: c.index } }];
"""

CLIPS_RECORD = DETAIL_JS + r"""
const a = $('Assemble').first().json;
const r = $input.first().json || {};
const ok = r.statusCode === 201 && r.body && r.body.id;
return [{ json: { index: a.index, title: a.title, range: a.range, secs: a.secs, video_url: a.video_url,
  id: ok ? r.body.id : null, status: ok ? r.body.status : null, problems: a.problems,
  error: ok ? null : `not saved (${r.statusCode ? 'HTTP ' + r.statusCode + ': ' : ''}${detailOf(r)})` } }];
"""

CLIPS_REPLY = r"""
const p = $('Prepare').first().json;
const rows = $input.all().map(i => i.json).sort((a, b) => a.index - b.index);
const saved = rows.filter(r => r.id);
const form = `${FORM}form/mkt-content-approval`;
const lines = [`Cut ${rows.length} clip${rows.length === 1 ? '' : 's'} from ${p.url} and saved ${saved.length} to the content calendar as video items (caption written for ${p.caption_channel}):`, ''];
for (const r of rows) {
  if (!r.id) { lines.push(`- clip ${r.index} "${r.title}" (${r.range}): ${r.error}`); continue; }
  lines.push(`- #${r.id} "${r.title}" (${r.range}, ${r.secs} s) → ${r.status}` +
    (r.problems.length ? ` · needs a person: ${r.problems.join('; ').slice(0, 200)}` : ''));
  lines.push(`  ${r.video_url}`);
}
if (p.dropped.length) lines.push('', `Ignored channels: ${p.dropped.join(', ')}.`);
lines.push('', `Review and approve them here: ${form}`,
  'Nothing is published until a person approves it. Check each clip before approving: you need the rights to everything in the video.');
return [{ json: { result: lines.join('\n') } }];
""".replace("${FORM}", FORM_URL)


def wait(wf, name, seconds, pos=None):
    return wf.add(name, "n8n-nodes-base.wait", 1.1, {"amount": seconds, "unit": "seconds"}, pos=pos,
                  webhookId=_uid(f"{wf.id}/{name}/webhook"))


def wf77():
    wf = Workflow(77, "Tool · Clips from a video")
    s = sub_trigger(wf, [("url", "string"), ("max_clips", "string"), ("channels", "string"), ("topic", "string")])
    p = code(wf, "Prepare", CLIPS_PREPARE)
    ok = if_true(wf, "Link ok?", "={{ $json.ok }}")
    bad = code(wf, "Explain", "return [{ json: { result: $input.first().json.result } }];", pos=[wf._x, 520])
    sub = http(wf, "Submit job", "POST", "={{ $env.CLIPS_URL }}/jobs",
               "={{ JSON.stringify({ url: $('Prepare').first().json.url, max_clips: $('Prepare').first().json.max_clips }) }}",
               key=True, full_response=True, never_error=True, continue_on_fail=True, timeout=60000)
    sd = code(wf, "Submitted", CLIPS_SUBMITTED)
    qd = if_true(wf, "Queued?", "={{ $json.state === 'wait' }}")
    busy = if_true(wf, "Busy?", "={{ $json.state === 'busy' }}", pos=[wf._x, 520])
    retry = wait(wf, "Busy, retry later", BUSY_RETRY_S, pos=[wf._x + 240, 520])
    refused = code(wf, "Refused", "return [{ json: { result: $input.first().json.result } }];", pos=[wf._x + 240, 720])
    wt = wait(wf, "Wait for clips", POLL_S)
    gj = http(wf, "Get job", "GET", "={{ $env.CLIPS_URL }}/jobs/{{ $json.id }}", key=True,
              full_response=True, never_error=True, continue_on_fail=True, timeout=30000)
    js = code(wf, "Job state", CLIPS_JOB_STATE)
    running = if_true(wf, "Still running?", "={{ $json.state === 'wait' }}")
    ready = if_true(wf, "Clips ready?", "={{ $json.state === 'done' }}")
    failed = code(wf, "Job failed", "return [{ json: { result: $input.first().json.result } }];", pos=[wf._x, 520])
    each = code(wf, "One per clip", CLIPS_EACH)
    loop = loop_one_by_one(wf, "Loop clips")
    cv = code(wf, "Caption request", CLIPS_CAPTION_VARS)
    wc = gateway(wf, "Write caption", "social_posts", "$json.vars", continue_on_fail=True)
    fg = code(wf, "For the gate", CLIPS_FOR_GATE)
    has = if_true(wf, "Caption written?", "={{ $json.ok }}")
    # Report only: the clip's words are fixed, a rewrite could drift from what was said.
    q = call_workflow(wf, "Quality gate", 35, {"text": "={{ $json.text }}", "channel": "={{ $json.channel }}",
                                               "context": "={{ $json.context }}", "ref": "={{ $json.ref }}",
                                               "rewrite": "no"}, pos=[wf._x, 200])
    asm = code(wf, "Assemble", CLIPS_ASSEMBLE)
    sv = http(wf, "Save clip", "POST", "={{ $env.CALENDAR_URL }}/items",
              "={{ JSON.stringify({ title: $json.title, channel: $json.channel, body: $json.body, status: $json.status, "
              "notes: $json.notes, video_url: $json.video_url, image_url: $json.image_url, hook_style: $json.hook_style }) }}",
              key=True, full_response=True, never_error=True, continue_on_fail=True, timeout=30000)
    rec = code(wf, "Record", CLIPS_RECORD)
    rp = code(wf, "Reply", CLIPS_REPLY, pos=[wf._x, 100])

    wf.chain(s, p, ok)
    wf.link(ok, sub, src_index=0)
    wf.link(ok, bad, src_index=1)
    wf.chain(sub, sd, qd)
    wf.link(qd, wt, src_index=0)
    wf.link(qd, busy, src_index=1)
    wf.link(busy, retry, src_index=0)     # 429: another video is being cut; submit again
    wf.link(busy, refused, src_index=1)   # 422 (platform link, not media, …) and the rest
    wf.link(retry, sub)
    wf.chain(wt, gj, js, running)
    wf.link(running, wt, src_index=0)     # poll again
    wf.link(running, ready, src_index=1)
    wf.link(ready, each, src_index=0)
    wf.link(ready, failed, src_index=1)
    wf.link(each, loop)
    wf.link(loop, rp, src_index=0)        # done: every recorded clip
    wf.link(loop, cv, src_index=1)        # one clip per round (the local LLM is slow)
    wf.chain(cv, wc, fg, has)
    wf.link(has, q, src_index=0)
    wf.link(has, asm, src_index=1)        # no caption: saved as a draft that says so
    wf.chain(q, asm, sv, rec)
    wf.link(rec, loop)
    return wf, {
        "summary": "Turns one long video the user owns (a webinar, a talk, a podcast recording) into short vertical clips, each saved to the content calendar (19) as a `video` item for approval. It sends the direct link to the clip finder (73, `POST $CLIPS_URL/jobs`: transcription, clip scoring, 9:16 render with burned-in captions) and polls the job every 20 s (`GET /jobs/{id}`; a job takes minutes: about 4 for a 3-minute talk on a laptop) until it is done, failed, or `CLIPS_MAX_WAIT_MIN` has passed. While another video is being cut (73 answers 429) it retries every 30 s within the same limit. A link to a video platform (YouTube, TikTok, Instagram, Vimeo, …) is refused by 73 with 422 (their terms forbid downloading): the tool returns that message and asks for a direct link to the file. For each clip, one at a time, it writes a caption through the gateway (03, prompt `social_posts` for the first channel asked for, default `instagram, tiktok`; topic = the clip's title and the start of its transcript; the clip's transcript is the only source, the gateway adds the approved facts), runs it through the quality gate (35, report only, with the transcript as evidence for the claim checker: it is the user's own content) and saves it: title = the clip's title, body = the caption only (it is what gets published), `video_url` = the clip MP4, `image_url` = its poster, status `in_review` if the gate passed and `draft` with the problems otherwise, notes `clip m:ss–m:ss from <source> (score N)`, `video preview: <clip url> (<n> s)` and the job id. Editing the caption in the approval form (38) or the control room (72) keeps the clip (it is not a video script, nothing is re-rendered). The publisher (39) sends the clip as `video_url`; `54-postiz-bridge` fetches clips under `CLIPS_PUBLIC_URL` from `CLIPS_INTERNAL_URL` and posts them to the integration mapped to `video` in its `CHANNEL_MAP`.",
        "inputs": {"url": "direct http(s) link to the video or audio file (not a YouTube/TikTok/… page)",
                   "max_clips": "optional; how many clips, default 3, at most `CLIPS_MAX_PER_VIDEO`",
                   "channels": "optional; comma list of instagram, tiktok, youtube, facebook, x, linkedin, threads (default `instagram, tiktok`); the caption is written for the first",
                   "topic": "optional; what the video is about, context for the captions"},
        "output": "`{result}`: the calendar items created (id, title, time range, status, clip URL) and the approval link, or why no clips were made",
        "env": {"CLIPS_URL": "the clip finder (73), `http://clip-finder:8000` in the stack; empty = the tool says it is not set up",
                "CLIPS_MAX_WAIT_MIN": "how long to wait for a job, default 30 minutes",
                "CLIPS_MAX_PER_VIDEO": "most clips per video, default 5 (73 accepts up to its `MAX_CLIPS`)",
                "N8N_PUBLIC_URL": "for the approval form link"},
        "depends": ["73-clip-finder", "03-llm-gateway", "04-prompt-library (prompt `social_posts`)",
                    "19-content-calendar", "35-wf-tool-quality-gate", "54-postiz-bridge (to publish, `CLIPS_PUBLIC_URL`)"],
        "called_by": "the chat agent (24), tool `clip_video`",
        "test": {"url": "https://cdn.example.com/webinars/2026-09.mp4", "max_clips": "3", "channels": "instagram, tiktok",
                 "topic": "our September webinar on office coffee rituals"},
    }


ALL_P9 = [wf77]
