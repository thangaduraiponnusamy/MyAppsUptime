import { Component, computed, input } from '@angular/core';

const W = 600;
const H = 180;

@Component({
  selector: 'app-area-chart',
  standalone: true,
  template: `
    <svg [attr.viewBox]="'0 0 ' + W + ' ' + H" preserveAspectRatio="none" class="area-chart">
      <defs>
        <linearGradient [attr.id]="gid" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#6366f1" stop-opacity=".28" />
          <stop offset="1" stop-color="#6366f1" stop-opacity="0" />
        </linearGradient>
        <linearGradient [attr.id]="gid + 's'" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stop-color="#6366f1" />
          <stop offset="1" stop-color="#06b6d4" />
        </linearGradient>
      </defs>
      @for (y of grid; track y) { <line x1="0" [attr.x2]="W" [attr.y1]="y" [attr.y2]="y" class="grid" /> }
      <path [attr.d]="area()" [attr.fill]="'url(#' + gid + ')'" />
      <path [attr.d]="line()" class="line" [attr.stroke]="'url(#' + gid + 's)'" />
    </svg>
    <div class="chart-axis">
      @for (l of labels(); track $index) { <span>{{ l }}</span> }
    </div>
  `,
})
export class AreaChartComponent {
  values = input<(number | null)[]>([]);
  axis = input<string[]>([]);
  readonly W = W;
  readonly H = H;
  readonly grid = [H * 0.25, H * 0.5, H * 0.75];
  readonly gid = `area-${Math.random().toString(36).slice(2, 8)}`;

  private points = computed(() => {
    const vals = this.values();
    const max = Math.max(1, ...vals.filter((v): v is number => v !== null)) * 1.15;
    const step = vals.length > 1 ? W / (vals.length - 1) : 0;
    return vals
      .map((v, i) => (v === null ? null : { x: i * step, y: H - (v / max) * H }))
      .filter((p): p is { x: number; y: number } => p !== null);
  });

  line = computed(() => {
    const pts = this.points();
    if (pts.length < 2) return '';
    // Smooth curve through points using cubic midpoints
    let d = `M${pts[0].x},${pts[0].y}`;
    for (let i = 1; i < pts.length; i++) {
      const mx = (pts[i - 1].x + pts[i].x) / 2;
      d += ` C${mx},${pts[i - 1].y} ${mx},${pts[i].y} ${pts[i].x},${pts[i].y}`;
    }
    return d;
  });

  area = computed(() => {
    const pts = this.points();
    return pts.length < 2 ? '' : `${this.line()} L${pts[pts.length - 1].x},${H} L${pts[0].x},${H} Z`;
  });

  labels = computed(() => {
    const a = this.axis();
    if (a.length <= 5) return a;
    const step = (a.length - 1) / 4;
    return [0, 1, 2, 3, 4].map((i) => a[Math.round(i * step)]);
  });
}
