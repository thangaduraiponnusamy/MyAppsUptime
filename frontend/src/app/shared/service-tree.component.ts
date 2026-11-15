import { Component, computed, input, output, signal } from '@angular/core';
import { Monitor, Project } from '../core/models';

interface Group { id: number; name: string; items: Monitor[]; shown: Monitor[] }

const PAGE = 30;

/**
 * Services grouped by project, each project a collapsible row.
 * With `whole` set, ticking a project means the whole project (also services added to it later);
 * without it, ticking a project ticks all of its services.
 */
@Component({
  selector: 'app-service-tree',
  standalone: true,
  template: `
    <div class="tree-bar">
      <input class="search" [value]="query()" (input)="setQuery($any($event.target).value)" placeholder="Search project or service..." />
      <label class="check"><input type="checkbox" [checked]="onlyPicked()" (change)="onlyPicked.set(!onlyPicked())" /> Only selected</label>
      <span class="muted small">{{ summary() }}</span>
    </div>
    <div class="tree">
      @for (g of groups().slice(0, limit()); track g.id) {
        <div class="tree-group" [class.open]="isOpen(g.id)">
          <div class="tree-head">
            <button type="button" class="tree-caret" (click)="toggleOpen(g.id)" [attr.aria-label]="(isOpen(g.id) ? 'Collapse ' : 'Expand ') + g.name">{{ isOpen(g.id) ? '▾' : '▸' }}</button>
            <input type="checkbox" [checked]="state(g) === 'all'" [indeterminate]="state(g) === 'some'" (change)="toggleGroup(g)" [attr.aria-label]="g.name" />
            <span class="tree-name" (click)="toggleOpen(g.id)">{{ g.name }}</span>
            <span class="muted small">{{ label(g) }}</span>
          </div>
          @if (isOpen(g.id)) {
            <div class="tree-items">
              @for (m of g.shown; track m.id) {
                <label class="check">
                  <input type="checkbox" [checked]="isWhole(g.id) || picked().has(m.id)" [disabled]="isWhole(g.id)" (change)="toggleService(m.id)" />
                  {{ m.name }} <small class="muted">{{ m.target }}</small>
                </label>
              } @empty { <span class="muted small">No services in this project.</span> }
            </div>
          }
        </div>
      } @empty {
        <p class="muted small">Nothing found.</p>
      }
      @if (groups().length > limit()) {
        <button type="button" class="btn sm" (click)="limit.set(limit() + PAGE)">Show more projects ({{ groups().length - limit() }} left)</button>
      }
    </div>
  `,
  styles: `
    .tree-bar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
    .tree { border: 1px solid var(--border); border-radius: 10px; max-height: 360px; overflow-y: auto; padding: 4px; }
    .tree-group + .tree-group { border-top: 1px solid var(--border); }
    .tree-head { display: flex; align-items: center; gap: 8px; padding: 6px 8px; }
    .tree-head input, .tree-items input, .tree-bar input[type=checkbox] { width: auto; }
    .tree-caret { border: none; background: none; color: var(--muted); cursor: pointer; width: 20px; font-size: 13px; padding: 0; }
    .tree-name { font-weight: 600; color: var(--text); cursor: pointer; }
    .tree-items { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 6px; padding: 4px 8px 10px 36px; }
    .tree-items label { flex-direction: row; align-items: center; gap: 6px; color: var(--text); }
    .tree-items small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .tree-bar .check { flex-direction: row; align-items: center; gap: 6px; }
  `,
})
export class ServiceTreeComponent {
  readonly PAGE = PAGE;
  projects = input.required<Project[]>();
  monitors = input.required<Monitor[]>();
  picked = input.required<Set<number>>();
  /** Project ids picked as a whole; null = no whole-project mode */
  whole = input<number[] | null>(null);
  /** Project listed first (the one being edited) */
  firstId = input<number | null>(null);
  pickedChange = output<Set<number>>();
  wholeChange = output<number[]>();

  query = signal('');
  onlyPicked = signal(false);
  limit = signal(PAGE);
  private opened = signal(new Set<number>());

  private all = computed<Group[]>(() => {
    const byProject = new Map<number, Monitor[]>();
    for (const m of this.monitors()) {
      const id = m.project_id ?? 0;
      byProject.set(id, [...(byProject.get(id) ?? []), m]);
    }
    const groups = this.projects().map((p) => ({ id: p.id!, name: p.name, items: byProject.get(p.id!) ?? [], shown: [] as Monitor[] }));
    if (byProject.has(0)) groups.push({ id: 0, name: 'No project', items: byProject.get(0)!, shown: [] });
    const first = this.firstId();
    return groups.sort((a, b) => Number(b.id === first) - Number(a.id === first) || a.name.localeCompare(b.name));
  });

  groups = computed<Group[]>(() => {
    const q = this.query().trim().toLowerCase();
    const only = this.onlyPicked();
    const picked = this.picked();
    const result: Group[] = [];
    for (const g of this.all()) {
      const nameHit = !q || g.name.toLowerCase().includes(q);
      let shown = nameHit ? g.items : g.items.filter((m) => m.name.toLowerCase().includes(q) || m.target.toLowerCase().includes(q));
      if (only && !this.isWhole(g.id)) shown = shown.filter((m) => picked.has(m.id));
      if (only && !this.isWhole(g.id) && !shown.length) continue;
      if (!nameHit && !shown.length) continue;
      result.push({ ...g, shown });
    }
    return result;
  });

  summary = computed(() => {
    const whole = this.whole();
    const services = this.picked().size;
    const s = `${services} ${services === 1 ? 'service' : 'services'}`;
    return whole ? `${whole.length} whole ${whole.length === 1 ? 'project' : 'projects'}, ${s}` : `${s} selected`;
  });

  setQuery(value: string): void {
    this.query.set(value);
    this.limit.set(PAGE);
  }

  /** Searching opens the groups that matched by service name */
  isOpen(id: number): boolean {
    return this.opened().has(id) || (!!this.query().trim() && this.groups().some((g) => g.id === id && g.shown.length > 0 && g.shown.length < g.items.length));
  }

  toggleOpen(id: number): void {
    const next = new Set(this.opened());
    next.has(id) ? next.delete(id) : next.add(id);
    this.opened.set(next);
  }

  isWhole(id: number): boolean {
    return this.whole()?.includes(id) ?? false;
  }

  state(g: Group): 'all' | 'some' | 'none' {
    if (this.isWhole(g.id)) return 'all';
    const n = g.items.filter((m) => this.picked().has(m.id)).length;
    if (this.whole() === null && n && n === g.items.length) return 'all';
    return n ? 'some' : 'none';
  }

  label(g: Group): string {
    if (this.isWhole(g.id)) return `whole project, ${g.items.length} ${g.items.length === 1 ? 'service' : 'services'}`;
    const n = g.items.filter((m) => this.picked().has(m.id)).length;
    return n ? `${n} of ${g.items.length}` : `${g.items.length}`;
  }

  toggleGroup(g: Group): void {
    const whole = this.whole();
    const next = new Set(this.picked());
    if (whole !== null) {
      const on = !whole.includes(g.id);
      this.wholeChange.emit(on ? [...whole, g.id] : whole.filter((id) => id !== g.id));
      for (const m of g.items) next.delete(m.id);  // the project covers them now, or nothing is left
    } else {
      const on = this.state(g) !== 'all';
      for (const m of g.items) on ? next.add(m.id) : next.delete(m.id);
    }
    this.pickedChange.emit(next);
  }

  toggleService(id: number): void {
    const next = new Set(this.picked());
    next.has(id) ? next.delete(id) : next.add(id);
    this.pickedChange.emit(next);
  }
}
