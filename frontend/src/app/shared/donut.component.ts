import { Component, computed, input } from '@angular/core';

export interface DonutSlice {
  label: string;
  value: number;
  color: string;
}

const R = 42;
const C = 2 * Math.PI * R;

@Component({
  selector: 'app-donut',
  standalone: true,
  template: `
    <div class="donut-wrap">
      <div class="donut">
        <svg viewBox="0 0 100 100">
          <circle cx="50" cy="50" [attr.r]="R" class="donut-track" />
          @for (s of arcs(); track s.label) {
            <circle cx="50" cy="50" [attr.r]="R" fill="none" [attr.stroke]="s.color" stroke-width="9" stroke-linecap="round"
                    [attr.stroke-dasharray]="s.len + ' ' + (C - s.len)" [attr.stroke-dashoffset]="-s.offset"
                    transform="rotate(-90 50 50)" />
          }
        </svg>
        <div class="donut-center">
          <b>{{ centerValue() ?? total() }}</b>
          <span>{{ caption() }}</span>
        </div>
      </div>
      <div class="legend-grid">
        @for (s of slices(); track s.label) {
          <div class="legend-tile" [class.zero]="!s.value" [style.--c]="s.color">
            <span class="lt-label"><i></i>{{ s.label }}</span>
            <span class="lt-value">{{ s.value }}<small>{{ pct(s.value) }}%</small></span>
          </div>
        }
      </div>
    </div>
  `,
})
export class DonutComponent {
  slices = input<DonutSlice[]>([]);
  caption = input('total');
  centerValue = input<string | null>(null);
  readonly R = R;
  readonly C = C;

  total = computed(() => this.slices().reduce((a, s) => a + s.value, 0));

  pct(value: number): number {
    return this.total() ? Math.round((value / this.total()) * 100) : 0;
  }

  arcs = computed(() => {
    const total = this.total() || 1;
    let offset = 0;
    return this.slices()
      .filter((s) => s.value > 0)
      .map((s) => {
        const len = (s.value / total) * C;
        const arc = { label: s.label, color: s.color, len: Math.max(0, len - 1.5), offset };
        offset += len;
        return arc;
      });
  });
}
