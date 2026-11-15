import { Component, ElementRef, ViewChild, computed, forwardRef, inject, input, model, signal } from '@angular/core';
import { ControlValueAccessor, NG_VALUE_ACCESSOR } from '@angular/forms';
import { MAX_SHOWN, Option, filterOptions } from './multi-select.component';

/** Single-choice dropdown with search. Use with ngModel, formControlName or [(value)]. */
@Component({
  selector: 'app-select',
  standalone: true,
  host: { '(document:click)': 'outside($event)', '(keydown.escape)': 'open.set(false)', '[class.bare]': 'bare()' },
  providers: [{ provide: NG_VALUE_ACCESSOR, useExisting: forwardRef(() => SelectComponent), multi: true }],
  template: `
    <button type="button" class="ms-box" [class.open]="open()" [disabled]="disabled()" (click)="toggle()" [attr.aria-label]="label() || null">
      <span class="ms-text" [class.muted]="!current()">{{ current()?.label ?? placeholder() }}</span>
      <span class="ms-caret">▾</span>
    </button>
    @if (open()) {
      <div class="ms-panel">
        <input #search class="search" [value]="query()" (input)="query.set($any($event.target).value)"
          (keydown.enter)="$event.preventDefault(); pickFirst()" placeholder="Search..." />
        <div class="ms-list">
          @for (o of shown(); track o.id) {
            <button type="button" class="ms-row" [class.on]="o.id === value()" (click)="choose(o.id)">{{ o.label }}</button>
          } @empty {
            <p class="muted small ms-empty">Nothing found.</p>
          }
          @if (more()) { <p class="muted small ms-empty">{{ more() }} more. Type to narrow the list.</p> }
        </div>
      </div>
    }
  `,
})
export class SelectComponent<T = number | null> implements ControlValueAccessor {
  options = input.required<Option<T>[]>();
  value = model<T | null>(null);
  placeholder = input('Pick...');
  label = input('');
  bare = input(false);

  open = signal(false);
  query = signal('');
  disabled = signal(false);
  private host = inject(ElementRef);
  private onChange: (v: T | null) => void = () => {};
  private onTouched: () => void = () => {};

  @ViewChild('search') set search(el: ElementRef<HTMLInputElement> | undefined) {
    el?.nativeElement.focus();
  }

  current = computed(() => this.options().find((o) => o.id === this.value()));
  private matches = computed(() => filterOptions(this.options(), this.query()));
  shown = computed(() => this.matches().slice(0, MAX_SHOWN));
  more = computed(() => Math.max(0, this.matches().length - MAX_SHOWN));

  toggle(): void {
    this.query.set('');
    this.open.set(!this.open());
  }

  choose(id: T): void {
    this.value.set(id);
    this.onChange(id);
    this.onTouched();
    this.open.set(false);
  }

  pickFirst(): void {
    const first = this.shown()[0];
    if (first) this.choose(first.id);
  }

  outside(e: Event): void {
    if (this.open() && !this.host.nativeElement.contains(e.target)) this.open.set(false);
  }

  writeValue(v: T | null): void { this.value.set(v); }
  registerOnChange(fn: (v: T | null) => void): void { this.onChange = fn; }
  registerOnTouched(fn: () => void): void { this.onTouched = fn; }
  setDisabledState(d: boolean): void { this.disabled.set(d); }
}
