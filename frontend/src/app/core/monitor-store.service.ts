import { Injectable, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { EMPTY, catchError, forkJoin, switchMap, timer } from 'rxjs';
import { ApiService } from './api.service';
import { AuthService } from './auth.service';
import { Monitor, Project, Summary } from './models';

@Injectable({ providedIn: 'root' })
export class MonitorStore {
  private api = inject(ApiService);
  private auth = inject(AuthService);
  readonly monitors = signal<Monitor[]>([]);
  readonly summary = signal<Summary | null>(null);
  readonly projects = signal<Project[]>([]);

  /** Poll only while signed in, at the interval set in Settings > General. */
  private readonly pollMs = computed(() => {
    const u = this.auth.user();
    return u && !u.must_change_password ? this.auth.refreshMs() : 0;
  });

  constructor() {
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
  }
}
