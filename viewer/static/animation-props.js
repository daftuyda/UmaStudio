import { segmentAt } from '/static/sequence.js';

export const setupKey = path => path?.startsWith('sequence:') ? 'sequence:' + path.slice(9).split('/').pop() : (path || '').split('/').pop();

export function propPlans(clip, key, saved) {
  const whole = saved[setupKey(key)];
  if (whole) return [{ clip, setups: whole, whole: true }];
  return (clip.segments || [{ clip }]).map(s => ({
    clip: s.clip, setups: saved[setupKey(s.clip.sourcePath)] || s.clip.propSetup || [], whole: false,
  }));
}

export function propFrame(clip, time, plans) {
  const segment = segmentAt(clip, time);
  const active = segment?.clip || clip;
  const plan = plans.find(p => p.whole ? p.clip === clip : p.clip === active);
  if (!plan) return null;
  const local = Math.max(0, time - (plan.whole ? 0 : segment?.start || 0));
  const events = plan.setup.events;
  const event = events?.findLast(e => e.time <= local);
  if (events && !event?.visible) return null;
  return { ...plan.setup, ...event, time: local };
}
