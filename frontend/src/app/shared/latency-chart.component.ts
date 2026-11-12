import { formatDate } from '@angular/common';
import { Component, computed, input, signal } from '@angular/core';
import { Heartbeat } from '../core/models';

const W = 800;
const H = 200;

@Component({
  selector: 'app-latency-chart',
  standalone: true,
  template: `
    <div class="chart-wrap" (mousemove)="move($event)" (mouseleave)="hover.set(-1)">
      <svg [attr.viewBox]="'0 0 ' + W + ' ' + H" preserveAspectRatio="none" class="chart">
        @for (y of grid; track y) {
          <line x1="0" [attr.x2]="W" [attr.y1]="y" [attr.y2]="y" class="grid" />
        }
        <path [attr.d]="area()" class="area" />
        <path [attr.d]="line()" class="line" />
        @for (x of downs(); track $index) {
          <rect [attr.x]="x" y="0" width="3" [attr.height]="H" class="down-mark" />
        }
        @if (point(); as p) {
          <line [attr.x1]="p.x" [attr.x2]="p.x" y1="0" [attr.y2]="H" class="cross" />
        }
      </svg>
      @if (point(); as p) {
        @if (p.y !== null) { <span class="chart-dot" [style.left.%]="p.x / W * 100" [style.top.%]="p.y / H * 100"></span> }
        <div class="chart-tip" [style.left.%]="tipLeft(p.x)">
          <div>{{ p.time }}</div>
          <b>{{ p.status === 'down' ? 'Down' : 'Ping ' + (p.ms ?? '--') + ' ms' }}</b>
        </div>
      }
    </div>
    <div class="chart-axis">@for (t of ticks(); track $index) { <span>{{ t }}</span> }</div>
    <div class="chart-legend"><span>0 ms</span><span>max {{ maxLatency() }} ms</span></div>
  `,
})
export class LatencyChartComponent {
  beats = input<Heartbeat[]>([]);
  readonly W = W;
  readonly H = H;
  readonly grid = [H * 0.25, H * 0.5, H * 0.75];
  hover = signal(-1);

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

  private fmt = computed(() => {
    const b = this.beats();
    const span = b.length > 1 ? Date.parse(b[b.length - 1].created_at) - Date.parse(b[0].created_at) : 0;
    return span > 86_400_000 ? 'MMM d, HH:mm' : 'HH:mm';
  });

  ticks = computed(() => {
    const b = this.beats();
    if (b.length < 2) return [];
    return [0, 0.25, 0.5, 0.75, 1].map((f) => formatDate(b[Math.round(f * (b.length - 1))].created_at, this.fmt(), 'en-US'));
  });

  point = computed(() => {
    const i = this.hover();
    const b = this.beats()[i];
    const p = this.points()[i];
    if (!b || !p) return null;
    const ms = b.latency_ms == null ? null : Math.round(b.latency_ms);
    return { x: p.x, y: p.y, ms, status: b.status, time: formatDate(b.created_at, 'yyyy-MM-dd HH:mm:ss', 'en-US') };
  });

  move(e: MouseEvent): void {
    const n = this.beats().length;
    const box = (e.currentTarget as HTMLElement).getBoundingClientRect();
    if (!n || !box.width) return;
    const f = Math.min(1, Math.max(0, (e.clientX - box.left) / box.width));
    this.hover.set(Math.round(f * (n - 1)));
  }

  tipLeft(x: number): number {
    return Math.min(88, Math.max(12, (x / W) * 100));
  }
}
