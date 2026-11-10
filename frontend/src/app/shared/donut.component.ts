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
    <div class="donut">
      <svg viewBox="0 0 100 100">
        <circle cx="50" cy="50" [attr.r]="R" class="donut-track" />
        @for (s of arcs(); track s.label) {
          <circle cx="50" cy="50" [attr.r]="R" fill="none" [attr.stroke]="s.color" stroke-width="11"
                  [attr.stroke-dasharray]="s.len + ' ' + (C - s.len)" [attr.stroke-dashoffset]="-s.offset"
                  transform="rotate(-90 50 50)" />
        }
      </svg>
      <div class="donut-center"><b>{{ total() }}</b><span>{{ caption() }}</span></div>
    </div>
    <ul class="donut-legend">
      @for (s of slices(); track s.label) {
        <li><i [style.background]="s.color"></i>{{ s.label }}<b>{{ s.value }}</b></li>
      }
    </ul>
  `,
})
export class DonutComponent {
  slices = input<DonutSlice[]>([]);
  caption = input('total');
  readonly R = R;
  readonly C = C;

  total = computed(() => this.slices().reduce((a, s) => a + s.value, 0));

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
