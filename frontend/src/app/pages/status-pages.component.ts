import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService, errorText } from '../core/api.service';
import { ConfirmService } from '../core/confirm.service';
import { StatusPage } from '../core/models';
import { MonitorStore } from '../core/monitor-store.service';

@Component({
  selector: 'app-status-pages',
  standalone: true,
  imports: [ReactiveFormsModule, RouterLink],
  template: `
    <section class="card">
      <h1>Status pages</h1>
      <p class="muted">Public, read-only pages you can share with users. No login needed.</p>
      <form [formGroup]="form" (ngSubmit)="save()" class="form-grid">
        <label>Title <input formControlName="title" placeholder="Platform Status" /></label>
        <label>Slug (URL) <input formControlName="slug" placeholder="platform" /></label>
        <label class="full">Description <input formControlName="description" /></label>
        <label>Custom domain (optional)
          <input formControlName="domain" placeholder="status.example.com" />
          <span class="hint">Point the domain's DNS to this server. Visitors to it see this page.</span>
        </label>
        <label>Logo (PNG, JPG, SVG, max 200 KB)
          <div class="row">
            @if (logo()) { <img class="sp-logo" [src]="logo()" alt="" /> }
            <input type="file" accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml" (change)="pickLogo($event)" />
            @if (logo()) { <button type="button" class="btn sm" (click)="logo.set(null)">Remove</button> }
          </div>
        </label>
        <div class="full check-list">
          @for (m of store.monitors(); track m.id) {
            <label><input type="checkbox" [checked]="selected().has(m.id)" (change)="toggle(m.id)" /> {{ m.name }}</label>
          }
        </div>
        @if (error()) { <p class="error full">{{ error() }}</p> }
        <div class="full row">
          <button class="btn primary" [disabled]="form.invalid || !selected().size">{{ editId() ? 'Save page' : 'Create page' }}</button>
          @if (editId()) { <button type="button" class="btn" (click)="cancel()">Cancel</button> }
        </div>
      </form>
    </section>

    <section class="card">
      <table>
        <thead><tr><th>Title</th><th>URL</th><th>Domain</th><th>Monitors</th><th></th></tr></thead>
        <tbody>
          @for (p of pages(); track p.id) {
            <tr>
              <td>{{ p.title }}</td>
              <td><a [routerLink]="['/status', p.slug]" target="_blank">/status/{{ p.slug }}</a></td>
              <td>{{ p.domain || '-' }}</td>
              <td>{{ p.monitor_ids.length }}</td>
              <td class="row">
                <button class="btn sm" (click)="edit(p)">Edit</button>
                <button class="btn sm danger" (click)="remove(p)">Delete</button>
              </td>
            </tr>
          } @empty {
            <tr><td colspan="5" class="muted">No status pages yet.</td></tr>
          }
        </tbody>
      </table>
    </section>
  `,
})
export class StatusPagesComponent {
  private api = inject(ApiService);
  store = inject(MonitorStore);
  private confirm = inject(ConfirmService);
  pages = signal<StatusPage[]>([]);
  selected = signal(new Set<number>());
  error = signal('');
  editId = signal<number | null>(null);
  logo = signal<string | null>(null);

  form = inject(FormBuilder).nonNullable.group({
    title: ['', Validators.required],
    slug: ['', [Validators.required, Validators.pattern(/^[a-z0-9-]{2,60}$/)]],
    description: [''],
    domain: ['', Validators.pattern(/^([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$/)],
  });

  constructor() {
    this.load();
  }

  load(): void {
    this.api.statusPages().subscribe((list) => this.pages.set(list));
  }

  toggle(id: number): void {
    const next = new Set(this.selected());
    next.has(id) ? next.delete(id) : next.add(id);
    this.selected.set(next);
  }

  edit(p: StatusPage): void {
    this.editId.set(p.id!);
    this.form.setValue({ title: p.title, slug: p.slug, description: p.description ?? '', domain: p.domain ?? '' });
    this.selected.set(new Set(p.monitor_ids));
    this.logo.set(p.logo);
    this.error.set('');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  cancel(): void {
    this.editId.set(null);
    this.form.reset();
    this.selected.set(new Set());
    this.logo.set(null);
    this.error.set('');
  }

  pickLogo(e: Event): void {
    const input = e.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    if (file.size > 200_000) {
      this.error.set('Logo must be 200 KB or smaller.');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => this.logo.set(reader.result as string);
    reader.readAsDataURL(file);
  }

  save(): void {
    const v = this.form.getRawValue();
    const body: StatusPage = {
      ...v, description: v.description || null, domain: v.domain.trim().toLowerCase() || null,
      logo: this.logo(), monitor_ids: [...this.selected()],
    };
    const id = this.editId();
    (id ? this.api.updateStatusPage(id, body) : this.api.createStatusPage(body))
      .subscribe({
        next: () => {
          this.cancel();
          this.load();
        },
        error: (e) => this.error.set(errorText(e)),
      });
  }

  async remove(p: StatusPage): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'Delete status page?',
      message: `You are about to delete the status page "${p.title}".`,
      details: [`The public link /status/${p.slug} stops working.`, 'Monitors are not deleted.'],
      typeToConfirm: p.slug,
      action: 'Delete page',
    });
    if (ok) this.api.deleteStatusPage(p.id!).subscribe(() => this.load());
  }
}
