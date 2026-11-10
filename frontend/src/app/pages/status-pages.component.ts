import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService, errorText } from '../core/api.service';
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
        <div class="full check-list">
          @for (m of store.monitors(); track m.id) {
            <label><input type="checkbox" [checked]="selected().has(m.id)" (change)="toggle(m.id)" /> {{ m.name }}</label>
          }
        </div>
        @if (error()) { <p class="error full">{{ error() }}</p> }
        <div class="full"><button class="btn primary" [disabled]="form.invalid || !selected().size">Create page</button></div>
      </form>
    </section>

    <section class="card">
      <table>
        <thead><tr><th>Title</th><th>URL</th><th>Monitors</th><th></th></tr></thead>
        <tbody>
          @for (p of pages(); track p.id) {
            <tr>
              <td>{{ p.title }}</td>
              <td><a [routerLink]="['/status', p.slug]" target="_blank">/status/{{ p.slug }}</a></td>
              <td>{{ p.monitor_ids.length }}</td>
              <td><button class="btn sm danger" (click)="remove(p.id!)">Delete</button></td>
            </tr>
          } @empty {
            <tr><td colspan="4" class="muted">No status pages yet.</td></tr>
          }
        </tbody>
      </table>
    </section>
  `,
})
export class StatusPagesComponent {
  private api = inject(ApiService);
  store = inject(MonitorStore);
  pages = signal<StatusPage[]>([]);
  selected = signal(new Set<number>());
  error = signal('');

  form = inject(FormBuilder).nonNullable.group({
    title: ['', Validators.required],
    slug: ['', [Validators.required, Validators.pattern(/^[a-z0-9-]{2,60}$/)]],
    description: [''],
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

  save(): void {
    const v = this.form.getRawValue();
    this.api
      .createStatusPage({ ...v, description: v.description || null, monitor_ids: [...this.selected()] })
      .subscribe({
        next: () => {
          this.form.reset();
          this.selected.set(new Set());
          this.error.set('');
          this.load();
        },
        error: (e) => this.error.set(errorText(e)),
      });
  }

  remove(id: number): void {
    this.api.deleteStatusPage(id).subscribe(() => this.load());
  }
}
