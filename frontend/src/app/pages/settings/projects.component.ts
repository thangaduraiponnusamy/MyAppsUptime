import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, errorText } from '../../core/api.service';
import { ConfirmService } from '../../core/confirm.service';
import { Channel, Project, User } from '../../core/models';
import { MonitorStore } from '../../core/monitor-store.service';
import { MultiSelectComponent, Option } from '../../shared/multi-select.component';

const EMAIL = -1;  // "Email to members" in the alerts dropdown

@Component({
  selector: 'app-settings-projects',
  standalone: true,
  imports: [FormsModule, MultiSelectComponent],
  template: `
    <h2 class="s-title">Projects</h2>
    <p class="muted">Every service belongs to one project. Members see only their projects (plus single services given to them in Users) and get email alerts for them.</p>

    <section class="s-section">
      <div class="s-head">
        <h3>All projects</h3>
        @if (!editing()) { <button class="btn primary sm" (click)="edit(null)">Add project</button> }
      </div>

      @if (editing(); as p) {
        <form class="s-fields s-box" (ngSubmit)="save()">
          <label>Name <input name="name" [(ngModel)]="p.name" placeholder="Project 1" required /></label>
          <label>Description <input name="desc" [(ngModel)]="p.description" /></label>

          <label>Members
            <app-multi-select [options]="userItems()" [picked]="members()" (pickedChange)="members.set($event)"
              placeholder="Pick members" empty="No users yet. Add them in Users." />
          </label>
          <label>Send alerts by
            <app-multi-select [options]="channelItems()" [picked]="chans()" (pickedChange)="chans.set($event)"
              placeholder="No alerts" />
            <small class="muted">Email to members, plus the channels added in Notifications (Telegram, Teams, ...).</small>
          </label>
          <div class="full">
            <span class="field-label">Services</span>
            @for (name of servicesOf(p.id); track $index) { <span class="tag svc">{{ name }}</span> }
            @empty { <span class="muted small">None yet.</span> }
            <div class="muted small">Services are added to a project when you create or edit a monitor.</div>
          </div>

          @if (error()) { <p class="error full">{{ error() }}</p> }
          <div class="row full">
            <button class="btn primary" [disabled]="!p.name">Save</button>
            <button type="button" class="btn" (click)="editing.set(null)">Cancel</button>
          </div>
        </form>
      }

      @if (projects().length > 8) {
        <input class="search" [value]="query()" (input)="query.set($any($event.target).value)" placeholder="Search project, service or member..." />
      }
      <table>
        <thead><tr><th>Project</th><th>Services</th><th class="hide-sm">Members</th><th class="hide-sm">Email</th><th class="hide-sm">Alerts</th><th></th></tr></thead>
        <tbody>
          @for (p of shown(); track p.id) {
            <tr>
              <td><b>{{ p.name }}</b> @if (p.description) { <div class="muted small">{{ p.description }}</div> }</td>
              <td>
                @for (name of servicesOf(p.id).slice(0, 6); track $index) { <span class="tag svc">{{ name }}</span> }
                @if (servicesOf(p.id).length > 6) { <span class="muted small">+{{ servicesOf(p.id).length - 6 }} more</span> }
                @if (!servicesOf(p.id).length) { <span class="muted">-</span> }
                @if (p.down_count) { <span class="pill down">{{ p.down_count }} down</span> }
              </td>
              <td class="hide-sm">{{ memberNames(p) || '-' }}</td>
              <td class="hide-sm">{{ p.email_members ? 'On' : 'Off' }}</td>
              <td class="hide-sm">{{ channelNames(p) }}</td>
              <td class="row nowrap">
                <button class="btn sm" (click)="edit(p)">Edit</button>
                <button class="btn sm danger" (click)="remove(p)">Delete</button>
              </td>
            </tr>
          } @empty {
            <tr><td colspan="6" class="empty">{{ query() ? 'No project matches.' : 'No projects yet.' }}</td></tr>
          }
        </tbody>
      </table>
    </section>
  `,
})
export class ProjectSettingsComponent {
  private api = inject(ApiService);
  store = inject(MonitorStore);
  private confirm = inject(ConfirmService);
  projects = signal<Project[]>([]);
  users = signal<User[]>([]);
  channels = signal<Channel[]>([]);
  editing = signal<Project | null>(null);
  members = signal(new Set<number>());
  chans = signal(new Set<number>());
  error = signal('');
  query = signal('');
  shown = computed(() => {
    const q = this.query().trim().toLowerCase();
    if (!q) return this.projects();
    return this.projects().filter((p) =>
      [p.name, p.description ?? '', this.memberNames(p), ...this.servicesOf(p.id)].some((s) => s.toLowerCase().includes(q)));
  });
  readonly general = 'General';
  private defaultChans = new Set<number>();
  private userById = computed(() => new Map(this.users().map((u) => [u.id!, u.name || u.username])));
  private services = computed(() => {
    const map = new Map<number | null, string[]>();
    for (const m of [...this.store.monitors()].sort((a, b) => a.name.localeCompare(b.name))) {
      map.set(m.project_id, [...(map.get(m.project_id) ?? []), m.name]);
    }
    return map;
  });

  userItems = computed<Option[]>(() => this.users().map((u) => ({ id: u.id!, label: u.name || u.username })));
  channelItems = computed<Option[]>(() => [
    { id: EMAIL, label: 'Email to members' },
    ...this.channels().map((c) => ({ id: c.id!, label: c.name })),
  ]);

  constructor() {
    this.load();
    this.api.users().subscribe((u) => this.users.set(u));
    this.api.channels().subscribe((c) => this.channels.set(c));
  }

  load(): void {
    this.api.projects().subscribe((p) => this.projects.set(p));
  }

  servicesOf(id: number | undefined): string[] {
    return id ? this.services().get(id) ?? [] : [];
  }

  memberNames(p: Project): string {
    return p.member_ids.map((id) => this.userById().get(id)).filter(Boolean).join(', ');
  }

  channelNames(p: Project): string {
    if (p.channel_ids == null) return 'Default';
    const names = this.channels().filter((c) => p.channel_ids!.includes(c.id!)).map((c) => c.name);
    return names.join(', ') || 'None';
  }

  edit(p: Project | null): void {
    const copy: Project = p
      ? { ...p, member_ids: [...p.member_ids], channel_ids: p.channel_ids ? [...p.channel_ids] : null }
      : { name: '', description: null, member_ids: [], email_members: true, channel_ids: null };
    this.members.set(new Set(copy.member_ids));
    // Default = channels set in Notifications for all projects or this one
    this.defaultChans = new Set(this.channels()
      .filter((c) => !c.project_ids.length || (!!p?.id && c.project_ids.includes(p.id))).map((c) => c.id!));
    const chans = new Set(copy.channel_ids ?? this.defaultChans);
    if (copy.email_members) chans.add(EMAIL);
    this.chans.set(chans);
    this.error.set('');
    this.editing.set(copy);
  }

  save(): void {
    const p = this.editing()!;
    p.member_ids = [...this.members()];
    p.email_members = this.chans().has(EMAIL);
    const chans = [...this.chans()].filter((id) => id !== EMAIL);
    const unchanged = p.channel_ids == null && chans.length === this.defaultChans.size && chans.every((id) => this.defaultChans.has(id));
    // Keep the default when nothing changed, so new channels for all projects still apply
    p.channel_ids = unchanged ? null : chans;
    const call = p.id ? this.api.updateProject(p.id, p) : this.api.createProject(p);
    call.subscribe({
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
      title: 'Delete project?',
      message: `You are about to delete the project "${p.name}".`,
      details: [
        `${p.monitor_count ?? 0} ${(p.monitor_count ?? 0) === 1 ? 'service is' : 'services are'} kept and moved to the ${this.general} project.`,
        `${p.member_ids.length} ${p.member_ids.length === 1 ? 'member loses' : 'members lose'} access to these monitors and stop getting their alerts.`,
      ],
      typeToConfirm: p.name,
      action: 'Delete project',
    });
    if (!ok) return;
    this.api.deleteProject(p.id!).subscribe({
      next: () => {
        this.load();
        this.store.refresh();
      },
      error: (e) => alert(errorText(e)),
    });
  }
}
