import { Component, inject, input, output } from '@angular/core';
import { MonitorStore } from '../core/monitor-store.service';
import { IconComponent } from './icon.component';

/** Project filter: null = all projects, 0 = monitors without a project. */
@Component({
  selector: 'app-project-pick',
  standalone: true,
  imports: [IconComponent],
  template: `
    @if (store.projects().length) {
      <label class="project-pick" [class.active]="project() !== null" title="Show one project">
        <app-icon name="folder" [size]="16" />
        <span class="pp-label">Project</span>
        <select (change)="onChange($any($event.target).value)">
          <option value="" [selected]="project() === null">All projects</option>
          @for (p of store.projects(); track p.id) { <option [value]="p.id" [selected]="project() === p.id">{{ p.name }}</option> }
          <option value="0" [selected]="project() === 0">No project</option>
        </select>
      </label>
    }
  `,
})
export class ProjectPickComponent {
  store = inject(MonitorStore);
  project = input<number | null>(null);
  pick = output<number | null>();

  onChange(value: string): void {
    this.pick.emit(value === '' ? null : Number(value));
  }
}

export function parseProject(value: string | null): number | null {
  return value !== null && /^\d+$/.test(value) ? Number(value) : null;
}
