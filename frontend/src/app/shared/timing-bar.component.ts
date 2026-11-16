import { Component, computed, input } from '@angular/core';
import { Timings } from '../core/models';

const PHASES: { key: keyof Timings; label: string; color: string }[] = [
  { key: 'dns', label: 'DNS', color: '#8b5cf6' },
  { key: 'connect', label: 'TCP', color: '#f59e0b' },
  { key: 'tls', label: 'TLS', color: '#ec4899' },
  { key: 'ttfb', label: 'TTFB', color: '#3b82f6' },
  { key: 'download', label: 'Download', color: '#10b981' },
];

/** One line of text, e.g. "DNS 3 ms, TCP 12 ms, TTFB 80 ms, total 98 ms". */
export function timingText(t: Timings | null | undefined): string {
  if (!t) return '';
  const parts = PHASES.filter(p => t[p.key] != null).map(p => `${p.label} ${Math.round(t[p.key]!)} ms`);
  return [...parts, `total ${Math.round(t.total)} ms`].join(', ');
}

@Component({
  selector: 'app-timing-bar',
  standalone: true,
  template: `
    <div class="tb-bar">
      @for (s of segments(); track s.label) {
        <span [style.width.%]="s.width" [style.background]="s.color" [title]="s.label + ' ' + s.ms + ' ms'"></span>
      }
    </div>
    <div class="tb-legend">
      @for (s of segments(); track s.label) {
        <span><i [style.background]="s.color"></i>{{ s.label }} {{ s.ms }} ms</span>
      }
      <span class="muted">Total {{ total() }} ms</span>
    </div>
  `,
  styles: [`
    .tb-bar { display: flex; height: 14px; border-radius: 4px; overflow: hidden; background: var(--border); }
    .tb-bar span { min-width: 2px; }
    .tb-legend { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 8px; font-size: 13px; }
    .tb-legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; }
  `],
})
export class TimingBarComponent {
  timings = input.required<Timings>();
  total = computed(() => Math.round(this.timings().total));

  segments = computed(() => {
    const t = this.timings();
    const sum = PHASES.reduce((a, p) => a + (t[p.key] ?? 0), 0) || 1;
    const scale = Math.max(sum, t.total);
    return PHASES.filter(p => t[p.key] != null).map(p => ({
      label: p.label, color: p.color, ms: Math.round(t[p.key]!), width: (t[p.key]! / scale) * 100,
    }));
  });
}
