import { Injectable, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { EMPTY, catchError, forkJoin, switchMap, timer } from 'rxjs';
import { ApiService } from './api.service';
import { Monitor, Summary } from './models';

@Injectable({ providedIn: 'root' })
export class MonitorStore {
  private api = inject(ApiService);
  readonly monitors = signal<Monitor[]>([]);
  readonly summary = signal<Summary | null>(null);

  constructor() {
    timer(0, 10_000)
      .pipe(switchMap(() => this.load().pipe(catchError(() => EMPTY))), takeUntilDestroyed())
      .subscribe((r) => this.apply(r));
  }

  refresh(): void {
    this.load().subscribe((r) => this.apply(r));
  }

  private load() {
    return forkJoin({ monitors: this.api.monitors(), summary: this.api.summary() });
  }

  private apply(r: { monitors: Monitor[]; summary: Summary }): void {
    this.monitors.set(r.monitors);
    this.summary.set(r.summary);
  }
}
