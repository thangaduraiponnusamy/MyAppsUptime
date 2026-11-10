import { Component, computed, input } from '@angular/core';

const PATHS: Record<string, string> = {
  pulse: 'M3 12h4l2-6 4 12 2-6h6',
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  alert: 'M12 3l9 16H3L12 3zM12 10v4M12 17v.5',
  wrench: 'M14 6a4 4 0 0 0 5 5l-8 8-3-3 8-8a4 4 0 0 0-2-2zM5 19l-1 1',
  bell: 'M6 16V11a6 6 0 1 1 12 0v5l2 2H4l2-2zM10 20a2 2 0 0 0 4 0',
  globe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18',
  cog: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2',
  plus: 'M12 5v14M5 12h14',
  check: 'M5 12l5 5 9-10',
  x: 'M6 6l12 12M18 6L6 18',
  clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2',
  lock: 'M6 11h12v9H6zM8 11V8a4 4 0 0 1 8 0v3',
  moon: 'M20 14A8 8 0 1 1 10 4a6 6 0 0 0 10 10z',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5',
  bolt: 'M13 2L4 14h7l-1 8 9-12h-7l1-8z',
  copy: 'M8 8h11v11H8zM5 16V5h11',
  menu: 'M4 6h16M4 12h16M4 18h16',
  list: 'M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01',
  activity: 'M3 12h4l3 8 4-16 3 8h4',
  server: 'M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01',
  arrow: 'M9 6l6 6-6 6',
  back: 'M15 6l-6 6 6 6',
  calendar: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
  sidebar: 'M4 4h16v16H4zM9 4v16',
};

@Component({
  selector: 'app-icon',
  standalone: true,
  template: `
    <svg [attr.width]="size()" [attr.height]="size()" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
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
