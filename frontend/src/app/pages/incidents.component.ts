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
                @for (a of i.acks ?? []; track $index) {
                  <div class="small"><b>{{ a.by }}</b> <span class="muted">{{ a.created_at | date: 'MMM d, HH:mm' }}</span>
                    @if (a.note) { &middot; {{ a.note }} }</div>
                } @empty {
                  @if (i.acknowledged_by) { <div class="small"><b>{{ i.acknowledged_by }}</b> <span class="muted">{{ i.note }}</span></div> }
                }
                @if (i.public_note && editing() !== i.id) { <div class="hint">Public: {{ i.public_note }}</div> }
                @if (editing() === i.id) {
                  <div class="row" style="flex-wrap: nowrap; margin-top: 6px">
                    <input [(ngModel)]="note" maxlength="1000" [placeholder]="i.acknowledged_at ? 'Add a note' : 'Note (optional)'" />
                    <button class="btn sm primary" [disabled]="i.acknowledged_at && !note.trim() && publicNote.trim() === (i.public_note ?? '')" (click)="ack(i)">Save</button>
                    <button class="btn sm" (click)="editing.set(null)">Cancel</button>
                  </div>
                  <input [(ngModel)]="publicNote" maxlength="1000" placeholder="Public note (shown on status pages)" style="margin-top: 6px" />
                } @else {
                  <button class="btn sm" style="margin-top: 4px" (click)="startAck(i)">{{ i.acknowledged_at ? 'Add note' : 'Acknowledge' }}</button>
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
  project = this.store.project;
  shown = computed(() => {
    const p = this.project();
    const of = this.store.projectOf();
    return p === null ? this.incidents() : this.incidents().filter((i) => (of.get(i.monitor_id) ?? 0) === p);
  });
  openOnly = signal(false);
  editing = signal<number | null>(null);
  note = '';
  publicNote = '';
  duration = duration;

  constructor() {
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe((p) => {
      const proj = parseProject(p.get('project'));
      if (proj !== null) {
        this.store.setProject(proj);
        this.router.navigate([], { relativeTo: this.route, queryParams: { project: null }, queryParamsHandling: 'merge', replaceUrl: true });
      }
    });
    this.load(false);
  }

  pickProject(project: number | null): void {
    this.store.setProject(project);
  }

  load(openOnly: boolean): void {
    this.openOnly.set(openOnly);
    this.api.incidents(openOnly).subscribe((list) => this.incidents.set(list));
  }

  startAck(i: Incident): void {
    this.note = '';
    this.publicNote = i.public_note ?? '';
    this.editing.set(i.id);
  }

  ack(i: Incident): void {
    this.api.acknowledge(i.id, this.note || null, this.publicNote.trim() || null).subscribe(() => {
      this.editing.set(null);
      this.load(this.openOnly());
      this.store.refresh();
    });
  }
}
