export function duration(fromIso: string, toIso: string | null): string {
  const ms = (toIso ? new Date(toIso).getTime() : Date.now()) - new Date(fromIso).getTime();
  const min = Math.max(0, Math.round(ms / 60_000));
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  if (h < 48) return `${h} h ${min % 60} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

// Short form for tight spaces: "45m", "3h 12m", "2d 4h"
export function short(fromIso: string, toIso: string | null = null): string {
  const ms = (toIso ? new Date(toIso).getTime() : Date.now()) - new Date(fromIso).getTime();
  const min = Math.max(0, Math.floor(ms / 60_000));
  if (min < 1) return '<1m';
  if (min < 60) return `${min}m`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}h ${min % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

export function daysUntil(iso: string | null): number | null {
  return iso ? Math.floor((new Date(iso).getTime() - Date.now()) / 86_400_000) : null;
}
