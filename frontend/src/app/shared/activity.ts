import { Incident } from '../core/models';
import { short } from './time';

export interface ActivityEvent {
  key: string;
  monitorId: number;
  name: string;
  kind: 'up' | 'down';
  at: string;
  detail: string;
  cause: string | null;
}

// Each incident = a "down" event, plus an "up" event when it was resolved
export function toEvents(incidents: Incident[]): ActivityEvent[] {
  const list: ActivityEvent[] = [];
  for (const i of incidents) {
    const name = i.monitor_name ?? `#${i.monitor_id}`;
    list.push({
      key: `d${i.id}`, monitorId: i.monitor_id, name, kind: 'down', at: i.started_at, cause: i.cause,
      detail: i.resolved_at ? `Down for ${short(i.started_at, i.resolved_at)}` : `Still down · ${short(i.started_at)}`,
    });
    if (i.resolved_at) {
      const next = incidents
        .filter((o) => o.monitor_id === i.monitor_id && o.started_at > i.resolved_at!)
        .sort((a, b) => a.started_at.localeCompare(b.started_at))[0];
      list.push({
        key: `u${i.id}`, monitorId: i.monitor_id, name, kind: 'up', at: i.resolved_at, cause: null,
        detail: next ? `Up for ${short(i.resolved_at, next.started_at)}` : `Up for ${short(i.resolved_at)} · running`,
      });
    }
  }
  return list.sort((a, b) => b.at.localeCompare(a.at));
}
