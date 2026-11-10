import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { ApiService } from '../core/api.service';
import { Incident } from '../core/models';
import { MonitorStore } from '../core/monitor-store.service';
import { ProjectPickComponent, parseProject } from '../shared/project-pick.component';
import { duration } from '../shared/time';

@Component({
  selector: 'app-incidents',
  standalone: true,
  imports: [DatePipe, FormsModule, RouterLink, ProjectPickComponent],
  template: `
    <div class="page-head">
      <div>
        <h1>Incidents</h1>
        <div class="sub">Opened automatically on DOWN, closed on recovery. Acknowledge to tell the team you are on it.</div>
      </div>
      <span class="spacer"></span>
      <app-project-pick [project]="project()" (pick)="pickProject($event)" />
      <div class="segmented">
        <button class="chip" [class.active]="!openOnly()" (click)="load(false)">All</button>
        <button class="chip" [class.active]="openOnly()" (click)="load(true)">Ongoing</button>
      </div>
    </div>

    <section class="card">
      <table>
        <thead><tr><th>Monitor</th><th>Cause</th><th>Started</th><th>Duration</th><th>State</th><th>Owner / note</th></tr></thead>
        <tbody>
          @for (i of shown(); track i.id) {
            <tr>
              <td><a [routerLink]="['/monitors', i.monitor_id]"><b>{{ i.monitor_name }}</b></a></td>
              <td class="muted">{{ i.cause }}</td>
              <td>{{ i.started_at | date: 'MMM d, HH:mm' }}</td>
              <td>{{ duration(i.started_at, i.resolved_at) }}</td>
              <td>
                @if (i.resolved_at) { <span class="pill up">resolved</span> }
                @else if (i.acknowledged_at) { <span class="pill degraded">acknowledged</span> }
                @else { <span class="pill down">open</span> }
              </td>
              <td>
                @if (editing() === i.id) {
                  <div class="row" style="flex-wrap: nowrap">
                    <input [(ngModel)]="by" placeholder="Your name" style="width: 120px" />
                    <input [(ngModel)]="note" placeholder="Note (optional)" />
                    <button class="btn sm primary" [disabled]="!by" (click)="ack(i)">Save</button>
                  </div>
                } @else if (i.acknowledged_by) {
                  <b>{{ i.acknowledged_by }}</b> <span class="muted">{{ i.note }}</span>
                  <button class="btn sm" (click)="startAck(i)">Edit</button>
                } @else {
                  <button class="btn sm" (click)="startAck(i)">Acknowledge</button>
                }
              </td>
            </tr>
          } @empty {
            <tr><td colspan="6" class="muted">No incidents. All good.</td></tr>
          }
        </tbody>
      </table>
    </section>
  `,
})
export class IncidentsComponent {
  private api = inject(ApiService);
  private store = inject(MonitorStore);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  incidents = signal<Incident[]>([]);
  project = signal<number | null>(null);
  shown = computed(() => {
    const p = this.project();
    const of = this.store.projectOf();
    return p === null ? this.incidents() : this.incidents().filter((i) => (of.get(i.monitor_id) ?? 0) === p);
  });
  openOnly = signal(false);
  editing = signal<number | null>(null);
  by = localStorage.getItem('ackName') ?? '';
  note = '';
  duration = duration;

  constructor() {
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe((p) => this.project.set(parseProject(p.get('project'))));
    this.load(false);
  }

  pickProject(project: number | null): void {
    this.router.navigate([], { relativeTo: this.route, queryParams: { project }, queryParamsHandling: 'merge', replaceUrl: true });
  }

  load(openOnly: boolean): void {
    this.openOnly.set(openOnly);
    this.api.incidents(openOnly).subscribe((list) => this.incidents.set(list));
  }

  startAck(i: Incident): void {
    this.note = i.note ?? '';
    this.editing.set(i.id);
  }

  ack(i: Incident): void {
    localStorage.setItem('ackName', this.by);
    this.api.acknowledge(i.id, this.by, this.note || null).subscribe(() => {
      this.editing.set(null);
      this.load(this.openOnly());
      this.store.refresh();
    });
  }
}
