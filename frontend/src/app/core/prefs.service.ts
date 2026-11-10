import { Injectable, effect, signal } from '@angular/core';

export interface Limit { key: 'recent' | 'page' | 'max'; label: string; hint: string; def: number; min: number; max: number; }

export const LIMITS: Limit[] = [
  { key: 'recent', label: 'Recent activity on dashboard', hint: 'Rows shown in the dashboard tile', def: 5, min: 1, max: 20 },
  { key: 'page', label: 'Activity rows per page', hint: 'Rows before "Show more"', def: 50, min: 10, max: 500 },
  { key: 'max', label: 'Activity history limit', hint: 'Most incidents loaded on the Activity page', def: 500, min: 50, max: 5000 },
];

const clamp = (l: Limit, v: unknown) => {
  const n = Math.round(Number(v));
  return Number.isFinite(n) ? Math.min(l.max, Math.max(l.min, n)) : l.def;
};

// Display limits, stored in this browser
@Injectable({ providedIn: 'root' })
export class PrefsService {
  readonly limits = signal(this.load());

  constructor() {
    effect(() => localStorage.setItem('limits', JSON.stringify(this.limits())));
  }

  set(l: Limit, value: unknown): void {
    this.limits.update((cur) => ({ ...cur, [l.key]: clamp(l, value) }));
  }

  reset(): void {
    this.limits.set(this.defaults());
  }

  private defaults(): Record<Limit['key'], number> {
    return Object.fromEntries(LIMITS.map((l) => [l.key, l.def])) as Record<Limit['key'], number>;
  }

  private load(): Record<Limit['key'], number> {
    let saved: Record<string, unknown> = {};
    try { saved = JSON.parse(localStorage.getItem('limits') ?? '{}'); } catch { /* bad value: use defaults */ }
    return Object.fromEntries(LIMITS.map((l) => [l.key, saved[l.key] === undefined ? l.def : clamp(l, saved[l.key])])) as Record<Limit['key'], number>;
  }
}
