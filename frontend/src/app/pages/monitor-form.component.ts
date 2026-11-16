import { Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { ApiService, errorText } from '../core/api.service';
import { MONITOR_TYPES, MonitorInput, MonitorType, Probe } from '../core/models';
import { MonitorStore } from '../core/monitor-store.service';
import { Option } from '../shared/multi-select.component';
import { SelectComponent } from '../shared/select.component';

const HTTP_TYPES: MonitorType[] = ['http', 'keyword', 'json'];
const URL_TYPES: MonitorType[] = [...HTTP_TYPES, 'security'];

@Component({
  selector: 'app-monitor-form',
  standalone: true,
  imports: [ReactiveFormsModule, RouterLink, SelectComponent],
  template: `
    <div class="page-head">
      <div>
        <h1>{{ id ? 'Edit monitor' : 'New monitor' }}</h1>
        <div class="sub">Pick a check type, a target and how you want to be alerted.</div>
      </div>
    </div>

    <section class="card">
      <form [formGroup]="form" (ngSubmit)="save()" class="form-grid">
        <label>Check type
          <select formControlName="type">
            @for (t of types; track t.value) { <option [value]="t.value">{{ t.label }}</option> }
          </select>
        </label>
        <label>Friendly name <input formControlName="name" placeholder="Payments API" /></label>
        @if (type() !== 'push') {
          <label>Run from
            <app-select formControlName="probe_id" [options]="probeOptions()" />
          </label>
        }

        @if (type() === 'docker') {
          <label class="full">Container name or ID
            <input formControlName="target" placeholder="pihole" />
          </label>
          <p class="hint full">Up = running, Degraded = unhealthy, Down = stopped or missing. Needs the Docker socket on the machine that runs the check
            (usually a private location; see Settings &gt; Private locations).</p>
        } @else if (type() !== 'push') {
          <label class="full">{{ isUrl() ? 'URL' : 'Hostname or IP' }}
            <input formControlName="target" [placeholder]="isUrl() ? 'https://example.com/health' : 'example.com'" />
          </label>
        } @else {
          <p class="hint full">A unique push URL is created after saving. Call it from your job; if no call arrives within the interval, the monitor goes DOWN.</p>
        }

        @if (type() === 'tcp' || type() === 'ssl') {
          <label>Port <input type="number" formControlName="port" [placeholder]="type() === 'ssl' ? '443' : ''" /></label>
        }
        @if (type() === 'keyword') {
          <label>Keyword <input formControlName="keyword" placeholder="healthy" /></label>
        }
        @if (type() === 'json') {
          <label>JSON path <input formControlName="json_path" placeholder="data.status or items.0.state" /></label>
          <label>Expected value (optional) <input formControlName="json_expected" placeholder="ok" /></label>
        }
        @if (isHttp()) {
          <label>Method
            <select formControlName="method"><option>GET</option><option>HEAD</option><option>POST</option></select>
          </label>
          <label>Accepted status codes <input formControlName="expected_status" placeholder="200-399" /></label>
        }
        @if (type() === 'security') {
          <label>Minimum grade
            <select formControlName="min_grade"><option>A</option><option>B</option><option>C</option><option>D</option></select>
          </label>
          <p class="muted full">Checks HTTPS, TLS version, HSTS, CSP and other security headers. A lower grade marks the monitor as degraded.</p>
        }
        @if (type() === 'ssl' || type() === 'security') {
          <label>Warn when expiring within (days) <input type="number" formControlName="ssl_expiry_warn_days" /></label>
        }

        <div class="section-title">Schedule</div>
        <label>Interval (seconds, min 20) <input type="number" min="20" formControlName="interval_sec" /></label>
        <label>Timeout (seconds, 1-60) <input type="number" min="1" max="60" formControlName="timeout_sec" /></label>
        <label>Retries before DOWN (0-5) <input type="number" min="0" max="5" formControlName="retries" /></label>
        <label>Depends on (parent monitor)
          <app-select formControlName="parent_id" [options]="parentOptions()" />
        </label>

        <div class="section-title">Performance &amp; SLA</div>
        <label>SLA target (%) <input type="number" step="0.01" formControlName="sla_target" /></label>
        <label>Slow response threshold (ms, optional) <input type="number" formControlName="latency_threshold_ms" /></label>
        <label class="inline full"><input type="checkbox" formControlName="anomaly_detection" />
          Smart latency anomaly detection (flags DEGRADED when response time stays far above the normal baseline for 3 checks in a row)</label>
        <label class="inline full"><input type="checkbox" formControlName="badge_enabled" />
          Enable badge (public SVG images anyone with the link can see; turning it off breaks old links)</label>

        <div class="section-title">Alerting</div>
        <label>Project (required)
          <app-select formControlName="project_id" [options]="projectOptions()"
            [placeholder]="store.projects().length ? 'Pick a project' : 'General (created automatically)'" />
        </label>
        <label>Repeat alert while down (minutes, optional) <input type="number" formControlName="notify_repeat_min" placeholder="30" /></label>
        <label>Alert after N failed checks in a row (optional)
          <input type="number" formControlName="down_after" placeholder="Default from Settings > General" /></label>
        <label>Tags (comma separated) <input formControlName="tags" placeholder="prod, api, payments" /></label>
        <p class="hint full">Every service belongs to a project. Project members and the project's channels get the alerts. Tags can narrow a channel further.
          Alerts are suppressed while the parent monitor is down.</p>

        @if (error()) { <p class="error full">{{ error() }}</p> }
        <div class="row full">
          <button class="btn primary" type="submit" [disabled]="form.invalid || saving()">Save monitor</button>
          <a class="btn" [routerLink]="id ? ['/monitors', id] : ['/']">Cancel</a>
        </div>
      </form>
    </section>
  `,
})
export class MonitorFormComponent {
  private fb = inject(FormBuilder);
  private api = inject(ApiService);
  private router = inject(Router);
  store = inject(MonitorStore);

  readonly types = MONITOR_TYPES;
  private route = inject(ActivatedRoute);
  id = Number(this.route.snapshot.paramMap.get('id')) || null;
  error = signal('');
  saving = signal(false);

  form = this.fb.nonNullable.group({
    name: ['', [Validators.required, Validators.maxLength(120)]],
    type: ['http' as MonitorType],
    target: [''],
    port: [null as number | null],
    keyword: [''],
    json_path: [''],
    json_expected: [''],
    method: ['GET' as MonitorInput['method']],
    expected_status: ['200-399'],
    interval_sec: [60, [Validators.min(20)]],
    timeout_sec: [10, [Validators.min(1), Validators.max(60)]],
    retries: [0, [Validators.min(0), Validators.max(5)]],
    down_after: [null as number | null, [Validators.min(1), Validators.max(20)]],
    latency_threshold_ms: [null as number | null],
    ssl_expiry_warn_days: [14],
    sla_target: [99.9, [Validators.min(50), Validators.max(100)]],
    anomaly_detection: [true],
    badge_enabled: [false],
    notify_repeat_min: [null as number | null, [Validators.min(5)]],
    parent_id: [null as number | null],
    project_id: [(Number(this.route.snapshot.queryParamMap.get('project')) || null) as number | null],
    probe_id: [null as number | null],
    min_grade: ['B' as MonitorInput['min_grade']],
    tags: [''],
  });

  type = toSignal(this.form.controls.type.valueChanges, { initialValue: 'http' as MonitorType });
  isUrl = computed(() => URL_TYPES.includes(this.type()));
  isHttp = computed(() => HTTP_TYPES.includes(this.type()));
  parents = computed(() => this.store.monitors().filter((m) => m.id !== this.id));
  probes = signal<Probe[]>([]);
  parentOptions = computed<Option<number | null>[]>(() => [{ id: null, label: 'None' },
    ...this.parents().map((m) => ({ id: m.id, label: m.name }))]);
  probeOptions = computed<Option<number | null>[]>(() => [{ id: null, label: 'This server' },
    ...this.probes().map((p) => ({ id: p.id, label: p.name + (p.online ? '' : ' (offline)') }))]);
  projectOptions = computed<Option<number | null>[]>(() => this.store.projects().map((p) => ({ id: p.id!, label: p.name })));

  constructor() {
    this.api.probes().subscribe((p) => this.probes.set(p));
    if (this.id) {
      this.api.monitor(this.id).subscribe((m) =>
        this.form.patchValue({
          ...m,
          keyword: m.keyword ?? '',
          json_path: m.json_path ?? '',
          json_expected: m.json_expected ?? '',
          tags: m.tags.join(', '),
          badge_enabled: !!m.badge_enabled,
        }),
      );
    }
  }

  save(): void {
    const v = this.form.getRawValue();
    if (v.project_id == null && this.store.projects().length) {
      this.error.set('Pick a project.');
      return;
    }
    const body: MonitorInput = {
      ...v,
      keyword: v.keyword || null,
      json_path: v.json_path || null,
      json_expected: v.json_expected || null,
      port: v.port || null,
      latency_threshold_ms: v.latency_threshold_ms || null,
      notify_repeat_min: v.notify_repeat_min || null,
      down_after: v.down_after || null,
      probe_id: v.type === 'push' ? null : v.probe_id,
      tags: v.tags.split(',').map((t) => t.trim()).filter(Boolean),
    };
    this.saving.set(true);
    const request = this.id ? this.api.updateMonitor(this.id, body) : this.api.createMonitor(body);
    request.subscribe({
      next: (m) => {
        this.store.refresh();
        this.router.navigate(['/monitors', m.id]);
      },
      error: (e) => {
        this.error.set(errorText(e));
        this.saving.set(false);
      },
    });
  }
}
