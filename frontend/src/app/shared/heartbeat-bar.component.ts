import { Component, computed, input } from '@angular/core';

@Component({
  selector: 'app-heartbeat-bar',
  standalone: true,
  template: `
    <div class="hb-bar" [class.sm]="size() === 'sm'">
      @for (s of padded(); track $index) {
        <span [class]="'beat ' + s" [title]="s"></span>
      }
    </div>
  `,
})
export class HeartbeatBarComponent {
  statuses = input<string[]>([]);
  max = input(50);
  size = input<'sm' | 'lg'>('lg');

  padded = computed(() => {
    const recent = this.statuses().slice(-this.max());
    return [...Array(this.max() - recent.length).fill('empty'), ...recent];
  });
}
