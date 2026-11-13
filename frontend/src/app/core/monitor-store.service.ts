import { Injectable, computed, effect, inject, signal, untracked } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { EMPTY, catchError, forkJoin, switchMap, timer } from 'rxjs';
import { ApiService } from './api.service';
import { AuthService } from './auth.service';
import { Monitor, Project, Summary } from './models';

const PICK_KEY = 'pickedProject';

@Injectable({ providedIn: 'root' })
export class MonitorStore {
  private api = inject(ApiService);
  private auth = inject(AuthService);
  readonly monitors = signal<Monitor[]>([]);
  readonly summary = signal<Summary | null>(null);
  readonly projects = signal<Project[]>([]);
  /** monitor id -> project id (0 = no project) */
  readonly projectOf = computed(() => new Map(this.monitors().map((m) => [m.id, m.project_id ?? 0])));

  /** Picked project filter per tenant, kept until the user picks "All projects". */
  private readonly picked = signal<Record<string, number>>(JSON.parse(localStorage.getItem(PICK_KEY) ?? '{}'));
  private readonly loaded = signal(false);
  readonly project = computed<number | null>(() => {
    const id = this.picked()[this.auth.user()?.active_tenant ?? ''] ?? null;
    return id !== null && this.loaded() && !this.projects().some((p) => p.id === id) ? null : id;
  });

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
    const next = { ...this.picked() };
    const tenant = this.auth.user()?.active_tenant ?? '';
    if (id) next[tenant] = id;
    else delete next[tenant];
    this.setAll(next);
  }

  private setAll(next: Record<string, number>): void {
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
