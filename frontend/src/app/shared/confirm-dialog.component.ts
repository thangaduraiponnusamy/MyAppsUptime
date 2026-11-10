import { Component, ElementRef, HostListener, effect, inject, signal, viewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ConfirmService } from '../core/confirm.service';
import { IconComponent } from './icon.component';

@Component({
  selector: 'app-confirm-dialog',
  standalone: true,
  imports: [FormsModule, IconComponent],
  template: `
    @if (confirm.current(); as c) {
      <div class="cd-backdrop" (click)="close(false)">
        <div class="cd-box" role="alertdialog" aria-modal="true" (click)="$event.stopPropagation()">
          <div class="cd-head">
            <span class="cd-ico"><app-icon name="alert" [size]="22" /></span>
            <h3>{{ c.title }}</h3>
          </div>
          <p>{{ c.message }}</p>
          @if (c.details?.length) {
            <ul class="cd-list">
              @for (d of c.details; track d) { <li>{{ d }}</li> }
            </ul>
          }
          <p class="cd-warn">This cannot be undone.</p>
          @if (c.typeToConfirm) {
            <label class="cd-type">
              <span>Type <b>{{ c.typeToConfirm }}</b> to confirm</span>
              <input #inp [ngModel]="typed()" (ngModelChange)="typed.set($event)" autocomplete="off" spellcheck="false" (keydown.enter)="ready() && close(true)" />
            </label>
          }
          <div class="cd-actions">
            <button class="btn" (click)="close(false)">Cancel</button>
            <button class="btn danger-solid" [disabled]="!ready()" (click)="close(true)">{{ c.action || 'Delete' }}</button>
          </div>
        </div>
      </div>
    }
  `,
})
export class ConfirmDialogComponent {
  confirm = inject(ConfirmService);
  typed = signal('');
  private input = viewChild<ElementRef<HTMLInputElement>>('inp');

  constructor() {
    effect(() => this.input()?.nativeElement.focus());
  }

  ready(): boolean {
    const want = this.confirm.current()?.typeToConfirm;
    return !want || this.typed().trim() === want;
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (this.confirm.current()) this.close(false);
  }

  close(ok: boolean): void {
    this.typed.set('');
    this.confirm.close(ok);
  }
}
