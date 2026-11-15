import { Component, computed, inject, input, output } from '@angular/core';
import { MonitorStore } from '../core/monitor-store.service';
import { IconComponent } from './icon.component';
import { Option } from './multi-select.component';
import { SelectComponent } from './select.component';

/** Project filter: null = all projects. */
@Component({
  selector: 'app-project-pick',
  standalone: true,
  imports: [IconComponent, SelectComponent],
  template: `
    @if (store.projects().length) {
      <div class="project-pick row" [class.active]="project() !== null" title="Show one project">
        <app-icon name="folder" [size]="16" />
        <app-select [bare]="true" label="Project" [options]="options()" [value]="project()" (valueChange)="pick.emit($event)" />
      </div>
    }
  `,
})
export class ProjectPickComponent {
  store = inject(MonitorStore);
  project = input<number | null>(null);
  pick = output<number | null>();
  options = computed<Option<number | null>[]>(() => [{ id: null, label: 'All projects' },
    ...this.store.projects().map((p) => ({ id: p.id!, label: p.name }))]);
}

export function parseProject(value: string | null): number | null {
  return value !== null && /^[1-9]\d*$/.test(value) ? Number(value) : null;
}
