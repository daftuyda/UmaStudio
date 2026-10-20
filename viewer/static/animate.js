import * as THREE from 'three';
import { $, store, toast, post, showTab } from '/static/ui.js';
import * as pose from '/static/pose.js';
import { JOINTS, CHANNELS, clone, frameTime, validateProject, sampleProject, sourceValue, putKey, projectClip } from '/static/animation-project.js';

let ctx, project = null, active = false, selected = null, clipboard = null, rootDraft = null;
let history = [], revision = 0, busy = false, importing = false;
const el = name => $('#anim' + name);
const time = () => ctx.current()?.action?.time || 0;
const persist = () => store.set('animationDraft', project);
const remember = () => { history.push(clone(project)); if (history.length > 40) history.shift(); };

function render() {
  el('Work').hidden = !project;
  el('Resume').hidden = !project || active;
  $('#authorTimeline').hidden = !active;
  if (!project) return;
  el('Source').hidden = !project.source;
  el('Source').textContent = project.source ? `Imported: ${project.source.name}. Keyframes add corrections over the original motion. Save a project to keep both.` : '';
  el('Name').value = project.name;
  el('Duration').value = Number(project.duration.toFixed(3));
  el('Fps').value = project.fps;
  el('Seek').step = el('KeyTime').step = 1 / project.fps;
  el('Seek').max = el('KeyTime').max = project.duration;
  el('Duration').min = 1 / project.fps;
  el('Loop').checked = project.loop;
  el('Tools').disabled = !active;
  el('Undo').disabled = !history.length;
  el('Copy').disabled = selected === null;
  el('Delete').disabled = selected === null || project.keys.length < 2;
  el('Move').disabled = selected === null;
  el('Paste').disabled = !clipboard;
  const key = project.keys.find(k => k.time === selected);
  el('KeyTime').value = selected ?? time();
  el('Ease').value = key?.ease || 'smooth';
  const lane = el('Keys');
  lane.replaceChildren();
  for (const k of project.keys) {
    const b = document.createElement('button');
    b.className = 'keyframe' + (k.time === selected ? ' selected' : '');
    b.style.left = `${100 * k.time / project.duration}%`;
    b.textContent = '◆';
    b.title = `${k.time.toFixed(2)} s · ${k.ease} — click to select`;
    b.setAttribute('aria-label', `Keyframe at ${k.time.toFixed(2)} seconds`);
    b.onclick = () => { seek(k.time); selected = k.time; render(); };
    lane.appendChild(b);
  }
  sync();
}

function discardDraft() {
  pose.clearDraft();
  rootDraft = null;
  $('#animationFace').checked = true;
  el('Note').textContent = 'Pose joints or choose a face, then Add / update keyframe. Seeking clears unkeyed changes.';
}

export function seek(t) {
  if (!active || busy || ctx.locked() || !Number.isFinite(t)) return;
  ctx.pause();
  discardDraft();
  ctx.seek(Math.max(0, Math.min(project.duration, frameTime(t, project.fps))));
  selected = project.keys.find(k => Math.abs(k.time - time()) < 1e-6)?.time ?? null;
  render();
}

async function activate(t = 0) {
  const mine = ctx.current();
  if (!mine) throw new Error('Choose a character first.');
  if ((mine.manifest.mini ? 'mini' : 'full') !== project.rig) throw new Error('This project uses a different skeleton. Choose a matching full-size or chibi character.');
  const request = ++revision;
  active = true;
  busy = true;
  discardDraft();
  $('#lookCam').value = 'off';
  pose.showJoints();
  try {
    const clip = projectClip(project, mine.rest);
    await ctx.play(clip);
    if (request !== revision || ctx.current() !== mine) return;
    const action = mine.action;
    action.setLoop(project.loop ? THREE.LoopRepeat : THREE.LoopOnce, project.loop ? Infinity : 1);
    action.clampWhenFinished = true;
    ctx.seek(Math.min(t, project.duration));
    persist();
  } catch (e) {
    if (request === revision) active = false;
    throw e;
  } finally {
    if (request === revision) { busy = false; render(); }
  }
}

async function change(next, t = time()) {
  if (busy) return;
  const validated = validateProject(next);
  remember();
  project = validated;
  await activate(t);
}

function capture() {
  const sampled = sampleProject(project, time());
  for (const [name, offset] of pose.offsets()) {
    if (JOINTS.includes(name)) sampled.rotations[name] = new THREE.Quaternion()
      .fromArray(sampled.rotations[name] || [0, 0, 0, 1]).multiply(offset).normalize().toArray();
  }
  if (rootDraft) sampled.root = [...rootDraft];
  if (!$('#animationFace').checked) Object.assign(sampled, ctx.face());
  return { ...sampled, time: frameTime(time(), project.fps), ease: el('Ease').value };
}

export function markDraft() {
  if (!active) return;
  ctx.pause();
  el('Note').textContent = 'Unkeyed changes — click Add / update keyframe to keep this pose.';
}

export function beforePlay() {
  if (!active) return;
  discardDraft();
  if (time() >= project.duration - 1e-7) { ctx.current()?.action?.reset(); ctx.seek(0); }
  if (ctx.current()?.action) ctx.current().action.enabled = true;
}

export function applyRoot() {
  if (!active || !rootDraft) return;
  const current = ctx.current(), bone = current?.root.getObjectByName('Position');
  if (bone && current.rest.has(bone)) bone.position.copy(current.rest.get(bone)[0])
    .add(new THREE.Vector3(...sourceValue(project, 'Position', 'position', time())))
    .add(new THREE.Vector3(...rootDraft));
}

export function deactivate() {
  if (!active) return;
  ++revision;
  busy = false;
  active = false;
  discardDraft();
  render();
}

export function sync() {
  if (!active || !project) return;
  const t = time();
  $('#animCursor').style.left = `${100 * t / project.duration}%`;
  el('Clock').textContent = `${t.toFixed(2)} / ${project.duration.toFixed(2)} s`;
  if (document.activeElement !== el('Seek')) el('Seek').value = t.toFixed(3);
  const root = rootDraft || sampleProject(project, t).root;
  ['X', 'Y', 'Z'].forEach((axis, i) => {
    if (document.activeElement !== el(axis)) el(axis).value = root[i].toFixed(3);
  });
}

export function initAnimate(context) {
  ctx = context;
  const guard = fn => async () => {
    if (ctx.locked() || busy || importing) return;
    try { await fn(); } catch (e) { toast(e.message, true); render(); }
  };
  el('New').onclick = guard(async () => {
    const current = ctx.current();
    if (!current) throw new Error('Choose a character first.');
    const rotations = {};
    for (const name of JOINTS) if (current.root.getObjectByName(name)) rotations[name] = [0, 0, 0, 1];
    const face = Object.fromEntries(CHANNELS.map(ch => [ch, [['Base', 1]]]));
    const mini = ctx.face().mini;
    const key = { time: 0, ease: 'smooth', rotations, root: [0, 0, 0], face, mini };
    project = validateProject({ format: 'UmaStudio animation', version: 1, name: 'My animation',
      rig: current.manifest.mini ? 'mini' : 'full', fps: 30, duration: 3, loop: true,
      keys: [key, { ...clone(key), time: 3 }] });
    history = []; selected = 0;
    await activate();
  });
  el('Resume').onclick = guard(() => activate());
  el('Add').onclick = guard(async () => {
    if (!active) return;
    const key = capture();
    selected = key.time;
    await change(putKey(project, key), key.time);
  });
  el('Copy').onclick = () => { clipboard = clone(project.keys.find(k => k.time === selected)); render(); };
  el('Paste').onclick = guard(async () => {
    if (!clipboard) return;
    const key = { ...clone(clipboard), time: frameTime(time(), project.fps) };
    selected = key.time;
    await change(putKey(project, key));
  });
  el('Delete').onclick = guard(async () => {
    if (project.keys.length <= 1 || selected === null) return;
    const keys = project.keys.filter(k => k.time !== selected);
    selected = null;
    await change({ ...project, keys });
  });
  el('Move').onclick = guard(async () => {
    const target = frameTime(Number(el('KeyTime').value), project.fps);
    if (!Number.isFinite(target) || target < 0 || target > project.duration) throw new Error('Choose a key time within the animation.');
    if (project.keys.some(k => k.time === target && k.time !== selected)) throw new Error('There is already a keyframe at that time.');
    const keys = project.keys.map(k => k.time === selected ? { ...k, time: target } : k);
    selected = target;
    await change({ ...project, keys }, target);
  });
  el('Ease').onchange = guard(async () => {
    if (selected === null) return;
    // Keep in-progress joint/face edits while changing the outgoing transition:
    // the next Add / update records both together.
    if (pose.offsets().size || rootDraft || !$('#animationFace').checked) {
      markDraft();
      return;
    }
    const keys = project.keys.map(k => k.time === selected ? { ...k, ease: el('Ease').value } : k);
    await change({ ...project, keys });
  });
  el('Undo').onclick = guard(async () => {
    if (!history.length) return;
    project = history.pop(); selected = null;
    await activate(time());
  });
  el('Apply').onclick = guard(async () => {
    const duration = frameTime(Number(el('Duration').value), Number(el('Fps').value));
    if (project.keys.some(k => k.time > duration)) throw new Error('Move or delete keyframes beyond the new duration first.');
    await change({ ...project, name: el('Name').value, duration, fps: Number(el('Fps').value), loop: el('Loop').checked });
  });
  el('Save').onclick = guard(async () => {
    const data = validateProject({ ...project, name: el('Name').value });
    const result = await post('/api/animation-project/save', data, 'Saving animation…');
    project = data; persist();
    toast('Saved animation project', false, ['Show folder', () => post('/api/open', { what: 'animations' })]);
    el('Note').textContent = `Saved: ${result.path}`;
  });
  el('Open').onclick = () => el('File').click();
  el('File').onchange = guard(async () => {
    const file = el('File').files[0];
    el('File').value = '';
    if (!file) return;
    if (file.size > 16 * 1024 * 1024) throw new Error('Animation projects must be smaller than 16 MB.');
    const next = validateProject(JSON.parse(await file.text()));
    const current = ctx.current();
    if (!current) throw new Error('Choose a character before opening an animation.');
    if ((current.manifest.mini ? 'mini' : 'full') !== next.rig) throw new Error('Choose a matching full-size or chibi character first.');
    if (project) remember();
    project = next; selected = null;
    await activate();
  });
  el('Pose').onclick = () => { pose.showJoints(); showTab('pose'); };
  el('Unity').value = store.get('animationUnity', '');
  el('Unity').onchange = () => store.set('animationUnity', el('Unity').value.trim());
  el('Import').onclick = () => el('ImportFile').click();
  el('ImportFile').onchange = guard(async () => {
    const file = el('ImportFile').files[0];
    el('ImportFile').value = '';
    if (!file) return;
    const model = ctx.current();
    if (!model || model.manifest.mini) throw new Error('Choose a full-size Uma before importing a Humanoid animation.');
    if (file.size > 32 * 1024 * 1024) throw new Error('Animation files must be smaller than 32 MB.');
    ctx.pause();
    importing = true;
    el('Import').disabled = true;
    const request = revision;
    try {
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = () => reject(new Error('Could not read this animation file.'));
        reader.readAsDataURL(file);
      });
      el('ImportStatus').textContent = 'Preparing animation…';
      const { id } = await post('/api/animation/import', { filename: file.name, data, bones: ctx.rig(), unity: el('Unity').value.trim() });
      let result;
      do {
        await new Promise(resolve => setTimeout(resolve, 750));
        result = await post('/api/animation/import/status', { id }, null);
        el('ImportStatus').textContent = result.message || '';
        if (result.state === 'failed') throw new Error(result.error);
      } while (result.state === 'running');
      if (ctx.current() !== model || revision !== request) throw new Error('The character or animation changed during import. Select the intended character and import again; the converted motion is cached.');
      const source = result.motion;
      const key = { time: 0, ease: 'linear', rotations: {}, root: [0, 0, 0],
        face: Object.fromEntries(CHANNELS.map(ch => [ch, [['Base', 1]]])), mini: null };
      const next = validateProject({ format: 'UmaStudio animation', version: 2, name: source.name,
        rig: 'full', fps: 60, duration: source.duration, loop: source.loop, source, keys: [key] });
      if (project) remember();
      project = next; selected = 0;
      await activate();
      el('ImportStatus').textContent = `Ready: ${source.name} · ${source.duration.toFixed(2)} s. Play below, or add correction keyframes.`;
      toast('Animation imported. Save a project to keep the editable motion.');
    } catch (e) {
      el('ImportStatus').textContent = e.message;
      throw e;
    } finally {
      importing = false; el('Import').disabled = false;
    }
  });
  el('Face').onclick = () => showTab('face');
  ['X', 'Y', 'Z'].forEach((axis, i) => {
    el(axis).oninput = () => {
      if (!active || ctx.locked()) return;
      const v = Number(el(axis).value);
      if (!Number.isFinite(v) || Math.abs(v) > 100) return;
      rootDraft ||= [...sampleProject(project, time()).root];
      rootDraft[i] = v; markDraft(); ctx.current()?.springs?.reset();
    };
  });
  el('Seek').onchange = () => seek(Number(el('Seek').value));
  el('Prev').onclick = () => seek(time() - 1 / project.fps);
  el('Next').onclick = () => seek(time() + 1 / project.fps);
  for (const id of ['faces', 'facePreset', 'faceBrow', 'faceEye', 'faceMouth']) {
    $('#' + id).addEventListener(id === 'faces' ? 'click' : 'change', () => {
      if (active) { $('#animationFace').checked = false; markDraft(); }
    });
  }
  try { const saved = store.get('animationDraft', null); if (saved) project = validateProject(saved); } catch { /* Ignore obsolete drafts. */ }
  render();
}

export const animationEditor = { get active() { return active; }, get project() { return project; }, seek, capture };
