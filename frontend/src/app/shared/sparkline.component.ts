import { Component, computed, input } from '@angular/core';

@Component({
  selector: 'app-sparkline',
  standalone: true,
  template: `
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" class="spark"><path [attr.d]="path()" /></svg>
  `,
})
export class SparklineComponent {
  values = input<(number | null)[]>([]);

  path = computed(() => {
    const vals = this.values().filter((v): v is number => v !== null);
    if (vals.length < 2) return '';
    const max = Math.max(...vals);
    const min = Math.min(...vals);
    const span = max - min || 1;
    return vals
      .map((v, i) => `${i ? 'L' : 'M'}${((i / (vals.length - 1)) * 100).toFixed(1)},${(28 - ((v - min) / span) * 26).toFixed(1)}`)
      .join(' ');
  });
}
