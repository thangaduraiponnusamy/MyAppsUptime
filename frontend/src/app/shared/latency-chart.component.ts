import { Component, computed, input } from '@angular/core';
import { Heartbeat } from '../core/models';

const W = 800;
const H = 200;

@Component({
  selector: 'app-latency-chart',
  standalone: true,
  template: `
    <svg [attr.viewBox]="'0 0 ' + W + ' ' + H" preserveAspectRatio="none" class="chart">
      @for (y of grid; track y) {
        <line x1="0" [attr.x2]="W" [attr.y1]="y" [attr.y2]="y" class="grid" />
      }
      <path [attr.d]="area()" class="area" />
      <path [attr.d]="line()" class="line" />
      @for (x of downs(); track $index) {
        <rect [attr.x]="x" y="0" width="3" [attr.height]="H" class="down-mark" />
      }
    </svg>
    <div class="chart-legend"><span>0 ms</span><span>max {{ maxLatency() }} ms</span></div>
  `,
})
export class LatencyChartComponent {
  beats = input<Heartbeat[]>([]);
  readonly W = W;
  readonly H = H;
  readonly grid = [H * 0.25, H * 0.5, H * 0.75];

  maxLatency = computed(() => {
    const max = this.beats().reduce((acc, b) => Math.max(acc, b.latency_ms ?? 0), 10);
    return Math.ceil(max / 50) * 50;
  });

  private points = computed(() => {
    const beats = this.beats();
    const max = this.maxLatency();
    const step = beats.length > 1 ? W / (beats.length - 1) : 0;
    return beats.map((b, i) => ({
      x: i * step,
      y: b.latency_ms == null ? null : H - (b.latency_ms / max) * (H - 10),
      down: b.status === 'down',
    }));
  });

  line = computed(() =>
    this.points()
      .filter((p) => p.y !== null)
      .map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y!.toFixed(1)}`)
      .join(' '),
  );

  area = computed(() => {
    const pts = this.points().filter((p) => p.y !== null);
    if (pts.length < 2) return '';
    return `${this.line()} L${pts[pts.length - 1].x.toFixed(1)},${H} L${pts[0].x.toFixed(1)},${H} Z`;
  });

  downs = computed(() => this.points().filter((p) => p.down).map((p) => p.x));
}
