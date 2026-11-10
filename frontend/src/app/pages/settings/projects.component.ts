import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { forkJoin, of, switchMap } from 'rxjs';
import { ApiService, errorText } from '../../core/api.service';
import { ConfirmService } from '../../core/confirm.service';
import { Project, User } from '../../core/models';
import { MonitorStore } from '../../core/monitor-store.service';

@Component({
  selector: 'app-settings-projects',
  standalone: true,
  imports: [FormsModule],
  template: `
    <h2 class="s-title">Teams</h2>
    <p class="muted">Group services by team. Members see only their teams and get email alerts for them.</p>

    <section class="s-section">
      <div class="s-head">
        <h3>All teams</h3>
        @if (!editing()) { <button class="btn primary sm" (click)="edit(null)">Add team</button> }
      </div>

      @if (editing(); as p) {
        <form class="s-fields s-box" (ngSubmit)="save()">
          <label>Name <input name="name" [(ngModel)]="p.name" placeholder="Team 1" required /></label>
          <label>Description <input name="desc" [(ngModel)]="p.description" /></label>

          <div class="full">
            <span class="field-label">Members</span>
            <div class="check-list">
              @for (u of users(); track u.id) {
                <label class="check"><input type="checkbox" [checked]="p.member_ids.includes(u.id!)" (change)="toggle(p.member_ids, u.id!)" />
                  {{ u.name || u.username }} @if (!u.email) { <small class="muted">(no email)</small> }</label>
              } @empty { <span class="muted small">No users yet. Add them in Users.</span> }
            </div>
          </div>
          <label class="check full"><input type="checkbox" name="em" [(ngModel)]="p.email_members" /> Email members when a monitor in this team goes down or recovers</label>

          <div class="full">
            <span class="field-label">Monitors ({{ picked().size }} selected)</span>
            <input class="search" name="q" [(ngModel)]="query" placeholder="Search monitors..." />
            <div class="check-list tall">
              @for (m of filtered(); track m.id) {
                <label class="check"><input type="checkbox" [checked]="picked().has(m.id)" (change)="pick(m.id)" />
                  {{ m.name }} @if (m.project_id && m.project_id !== p.id) { <small class="muted">(in {{ projectName(m.project_id) }})</small> }</label>
              } @empty { <span class="muted small">No monitors found.</span> }
            </div>
            <div class="row"><button type="button" class="btn sm" (click)="pickAll(true)">Select shown</button>
              <button type="button" class="btn sm" (click)="pickAll(false)">Clear shown</button></div>
          </div>

          @if (error()) { <p class="error full">{{ error() }}</p> }
          <div class="row full">
            <button class="btn primary" [disabled]="!p.name">Save</button>
            <button type="button" class="btn" (click)="editing.set(null)">Cancel</button>
          </div>
        </form>
      }

      <table>
        <thead><tr><th>Team</th><th>Monitors</th><th class="hide-sm">Members</th><th class="hide-sm">Email</th><th></th></tr></thead>
        <tbody>
          @for (p of projects(); track p.id) {
            <tr>
              <td><b>{{ p.name }}</b> @if (p.description) { <div class="muted small">{{ p.description }}</div> }</td>
              <td>{{ p.monitor_count }} @if (p.down_count) { <span class="pill down">{{ p.down_count }} down</span> }</td>
              <td class="hide-sm">{{ memberNames(p) || '-' }}</td>
              <td class="hide-sm">{{ p.email_members ? 'On' : 'Off' }}</td>
              <td class="row nowrap">
                <button class="btn sm" (click)="edit(p)">Edit</button>
                <button class="btn sm danger" (click)="remove(p)">Delete</button>
              </td>
            </tr>
          } @empty {
            <tr><td colspan="5" class="muted">No teams yet.</td></tr>
          }
        </tbody>
      </table>
    </section>
  `,
})
export class ProjectSettingsComponent {
  private api = inject(ApiService);
  private store = inject(MonitorStore);
  private confirm = inject(ConfirmService);
  projects = signal<Project[]>([]);
  users = signal<User[]>([]);
  editing = signal<Project | null>(null);
  picked = signal(new Set<number>());
  error = signal('');
  query = '';
  private original = new Set<number>();
  private userById = computed(() => new Map(this.users().map((u) => [u.id!, u.name || u.username])));

  constructor() {
    this.load();
    this.api.users().subscribe((u) => this.users.set(u));
  }

  load(): void {
    this.api.projects().subscribe((p) => this.projects.set(p));
  }

  filtered() {
    const q = this.query.toLowerCase();
    return this.store.monitors().filter((m) => !q || m.name.toLowerCase().includes(q) || m.target.toLowerCase().includes(q));
  }

  projectName(id: number): string {
    return this.projects().find((p) => p.id === id)?.name ?? '';
  }

  memberNames(p: Project): string {
    return p.member_ids.map((id) => this.userById().get(id)).filter(Boolean).join(', ');
  }

  edit(p: Project | null): void {
    const copy: Project = p ? { ...p, member_ids: [...p.member_ids] } : { name: '', description: null, member_ids: [], email_members: true };
    this.original = new Set(p ? this.store.monitors().filter((m) => m.project_id === p.id).map((m) => m.id) : []);
    this.picked.set(new Set(this.original));
    this.query = '';
    this.error.set('');
    this.editing.set(copy);
  }

  toggle(list: number[], id: number): void {
    const i = list.indexOf(id);
    if (i >= 0) list.splice(i, 1);
    else list.push(id);
  }

  pick(id: number): void {
    const next = new Set(this.picked());
    if (next.has(id)) next.delete(id);
    else next.add(id);
    this.picked.set(next);
  }

  pickAll(on: boolean): void {
    const next = new Set(this.picked());
    for (const m of this.filtered()) on ? next.add(m.id) : next.delete(m.id);
    this.picked.set(next);
  }

  save(): void {
    const p = this.editing()!;
    const call = p.id ? this.api.updateProject(p.id, p) : this.api.createProject(p);
    call
      .pipe(
        switchMap((saved) => {
          const add = [...this.picked()].filter((id) => !this.original.has(id));
          const drop = [...this.original].filter((id) => !this.picked().has(id));
          return forkJoin([
            add.length ? this.api.bulk(add, 'project', saved.id!) : of(null),
            drop.length ? this.api.bulk(drop, 'project', null) : of(null),
          ]);
        }),
      )
      .subscribe({
        next: () => {
          this.editing.set(null);
          this.load();
          this.store.refresh();
        },
        error: (e) => this.error.set(errorText(e)),
      });
  }

  async remove(p: Project): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'Delete team?',
      message: `You are about to delete the team "${p.name}".`,
      details: [
        `${p.monitor_count ?? 0} monitor(s) are kept but become unassigned.`,
        `${p.member_ids.length} member(s) lose access to these monitors and stop getting their alerts.`,
      ],
      typeToConfirm: p.name,
      action: 'Delete team',
    });
    if (!ok) return;
    this.api.deleteProject(p.id!).subscribe(() => {
      this.load();
      this.store.refresh();
    });
  }
}
