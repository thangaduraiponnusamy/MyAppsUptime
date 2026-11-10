import { Component, computed, input } from '@angular/core';

// Rounded rectangle as path data
const rr = (x: number, y: number, w: number, h: number, r = 1.5) =>
  `M${x + r} ${y}h${w - 2 * r}a${r} ${r} 0 0 1 ${r} ${r}v${h - 2 * r}a${r} ${r} 0 0 1-${r} ${r}h-${w - 2 * r}` +
  `a${r} ${r} 0 0 1-${r}-${r}v-${h - 2 * r}a${r} ${r} 0 0 1 ${r}-${r}z`;

const PATHS: Record<string, string> = {
  pulse: 'M3 12h4l2-6 4 12 2-6h6',
  grid: rr(3, 3, 7, 9) + rr(14, 3, 7, 5) + rr(14, 12, 7, 9) + rr(3, 16, 7, 5),
  alert: 'M21.73 18l-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3zM12 9v4M12 17h.01',
  wrench: 'M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z',
  bell: 'M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.94 1.94 0 0 0 3.4 0',
  globe: 'M12 2a10 10 0 1 0 0 20 10 10 0 1 0 0-20zM12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20M2 12h20',
  cog: 'M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4',
  plus: 'M12 5v14M5 12h14',
  check: 'M5 12l5 5 9-10',
  x: 'M6 6l12 12M18 6L6 18',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2',
  lock: 'M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3',
  moon: 'M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9z',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5',
  bolt: 'M13 2L4 14h7l-1 8 9-12h-7l1-8z',
  copy: 'M8 8h11v11H8zM5 16V5h11',
  menu: 'M4 6h16M4 12h16M4 18h16',
  list: 'M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01',
  activity: 'M3 12h4l3 8 4-16 3 8h4',
  server: rr(3, 3, 18, 7, 2) + rr(3, 14, 18, 7, 2) + 'M7 6.5h.01M7 17.5h.01',
  arrow: 'M9 6l6 6-6 6',
  back: 'M15 6l-6 6 6 6',
  calendar: rr(3, 5, 18, 16, 2) + 'M3 10h18M8 3v4M16 3v4',
  sidebar: rr(3, 3, 18, 18, 2) + 'M9 3v18',
  'panel-close': rr(3, 3, 18, 18, 2) + 'M9 3v18M16 15l-3-3 3-3',
  'panel-open': rr(3, 3, 18, 18, 2) + 'M9 3v18M14 9l3 3-3 3',
  child: 'M15 10l5 5-5 5M4 4v7a4 4 0 0 0 4 4h12',
  network: rr(16, 16, 6, 6, 1) + rr(2, 16, 6, 6, 1) + rr(9, 2, 6, 6, 1) + 'M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3M12 12V8',
};

@Component({
  selector: 'app-icon',
  standalone: true,
  template: `
    <svg [attr.width]="size()" [attr.height]="size()" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path [attr.d]="d()" />
    </svg>
  `,
  styles: [':host { display: inline-flex; }'],
})
export class IconComponent {
  name = input.required<string>();
  size = input(18);
  d = computed(() => PATHS[this.name()] ?? '');
}
