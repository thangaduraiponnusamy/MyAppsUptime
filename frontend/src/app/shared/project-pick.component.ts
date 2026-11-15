import { Component, computed, inject, input, output } from '@angular/core';
import { MonitorStore } from '../core/monitor-store.service';
import { IconComponent } from './icon.component';
import { MultiSelectComponent, Option } from './multi-select.component';

/** Project filter: nothing picked = all projects. */
@Component({
  selector: 'app-project-pick',
  standalone: true,
  imports: [IconComponent, MultiSelectComponent],
  template: `
    @if (store.projects().length) {
      <div class="project-pick row" [class.active]="picked().length > 0" title="Show one or more projects">
        <app-icon name="folder" [size]="16" />
        <app-multi-select placeholder="All projects" [options]="options()" [picked]="pickedSet()" (pickedChange)="emit($event)" />
      </div>
    }
  `,
})
export class ProjectPickComponent {
  store = inject(MonitorStore);
  picked = input<number[]>([]);
  pick = output<number[]>();
  pickedSet = computed(() => new Set(this.picked()));
  options = computed<Option<number>[]>(() => this.store.projects().map((p) => ({ id: p.id!, label: p.name })));

  emit(ids: Set<number>): void {
    this.pick.emit([...ids]);
  }
}

export function parseProject(value: string | null): number | null {
  return value !== null && /^[1-9]\d*$/.test(value) ? Number(value) : null;
}
