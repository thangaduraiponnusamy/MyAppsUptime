import { Injectable, computed, effect, inject, signal, untracked } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { EMPTY, catchError, forkJoin, switchMap, timer } from 'rxjs';
import { ApiService } from './api.service';
import { AuthService } from './auth.service';
import { Monitor, Project, Summary } from './models';

const PICK_KEY = 'pickedProject';

// Older versions saved one project id per tenant
function readPicked(): Record<string, number[]> {
  let raw: Record<string, unknown> = {};
  try { raw = JSON.parse(localStorage.getItem(PICK_KEY) ?? '{}') ?? {}; } catch { /* bad value: start empty */ }
  const out: Record<string, number[]> = {};
  for (const [tenant, v] of Object.entries(raw)) {
    const ids = (Array.isArray(v) ? v : [v]).filter((id): id is number => typeof id === 'number' && id > 0);
    if (ids.length) out[tenant] = ids;
  }
  return out;
}

@Injectable({ providedIn: 'root' })
export class MonitorStore {
  private api = inject(ApiService);
  private auth = inject(AuthService);
  readonly monitors = signal<Monitor[]>([]);
  readonly summary = signal<Summary | null>(null);
  readonly projects = signal<Project[]>([]);
  /** monitor id -> project id (0 = no project) */
  readonly projectOf = computed(() => new Map(this.monitors().map((m) => [m.id, m.project_id ?? 0])));

  /** Picked projects per tenant, kept until the user clears them (empty = all projects). */
  private readonly picked = signal<Record<string, number[]>>(readPicked());
  private readonly loaded = signal(false);
  readonly projectIds = computed<number[]>(() => {
    const ids = this.picked()[this.auth.user()?.active_tenant ?? ''] ?? [];
    if (!this.loaded()) return ids;
    const known = new Set(this.projects().map((p) => p.id));
    return ids.filter((id) => known.has(id));
  });
  /** Picked project ids, or null for all projects */
  readonly scope = computed<Set<number> | null>(() => (this.projectIds().length ? new Set(this.projectIds()) : null));
  /** The one picked project, or null when none or several are picked */
  readonly project = computed<number | null>(() => (this.projectIds().length === 1 ? this.projectIds()[0] : null));

  /** Poll only while signed in, at the interval set in Settings > General. */
  private readonly pollMs = computed(() => {
    const u = this.auth.user();
    return u && !u.must_change_password ? this.auth.refreshMs() : 0;
  });

  constructor() {
    effect(() => {
      if (this.auth.signIns()) untracked(() => this.setAll({}));
    });
    toObservable(this.pollMs)
      .pipe(
        switchMap((ms) => (ms ? timer(0, ms) : EMPTY)),
        switchMap(() => this.load().pipe(catchError(() => EMPTY))),
        takeUntilDestroyed(),
      )
      .subscribe((r) => this.apply(r));
  }

  refresh(): void {
    this.load().subscribe((r) => this.apply(r));
  }

  setProject(id: number | null): void {
    this.setProjects(id ? [id] : []);
  }

  setProjects(ids: Iterable<number>): void {
    const next = { ...this.picked() };
    const tenant = this.auth.user()?.active_tenant ?? '';
    const list = [...new Set(ids)].filter((id) => id > 0);
    if (list.length) next[tenant] = list;
    else delete next[tenant];
    this.setAll(next);
  }

  /** True when a monitor of this project passes the project filter */
  inScope(projectId: number | null | undefined): boolean {
    const s = this.scope();
    return s === null || s.has(projectId ?? 0);
  }

  private setAll(next: Record<string, number[]>): void {
    this.picked.set(next);
    localStorage.setItem(PICK_KEY, JSON.stringify(next));
  }

  projectName(id: number | null | undefined): string {
    return id ? (this.projects().find((p) => p.id === id)?.name ?? '') : '';
  }

  private load() {
    return forkJoin({ monitors: this.api.monitors(), summary: this.api.summary(), projects: this.api.projects() });
  }

  private apply(r: { monitors: Monitor[]; summary: Summary; projects: Project[] }): void {
    this.monitors.set(r.monitors);
    this.summary.set(r.summary);
    this.projects.set(r.projects);
    this.loaded.set(true);
  }
}
