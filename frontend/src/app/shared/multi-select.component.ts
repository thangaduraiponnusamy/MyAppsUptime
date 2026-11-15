import { Component, ElementRef, ViewChild, computed, inject, input, output, signal } from '@angular/core';

export interface Option<T = number> { id: T; label: string }

// Rows drawn at once; search narrows longer lists
export const MAX_SHOWN = 200;

// Items like "None", "All projects" or "Email" (null or negative id) stay on top
const pinned = (o: Option<unknown>) => o.id === null || (typeof o.id === 'number' && o.id < 0);

/** Search by label, then sort A-Z with numbers in order (job 2 before job 10) */
export function filterOptions<T>(options: Option<T>[], query: string): Option<T>[] {
  const q = query.trim().toLowerCase();
  return options
    .filter((o) => !q || o.label.toLowerCase().includes(q))
    .sort((a, b) => Number(pinned(b)) - Number(pinned(a)) || a.label.localeCompare(b.label, undefined, { numeric: true, sensitivity: 'base' }));
}

/** Dropdown with checkboxes and search; the closed box lists the picked names. */
@Component({
  selector: 'app-multi-select',
  standalone: true,
  host: { '(document:click)': 'outside($event)', '(keydown.escape)': 'open.set(false)' },
  template: `
    <button type="button" class="ms-box" [class.open]="open()" (click)="toggle()">
      <span class="ms-text" [class.muted]="!picked().size">{{ text() }}</span>
      @if (picked().size > 1) { <span class="ms-count">{{ picked().size }}</span> }
      <span class="ms-caret">▾</span>
    </button>
    @if (open()) {
      <div class="ms-panel">
        <input #search class="search" [value]="query()" (input)="query.set($any($event.target).value)" placeholder="Search..." />
        @if (matches().length > 1) {
          <div class="ms-actions">
            <button type="button" class="link-btn" (click)="setAll(true)">{{ query() ? 'Select found' : 'Select all' }}</button>
            <button type="button" class="link-btn" (click)="setAll(false)">{{ query() ? 'Clear found' : 'Clear' }}</button>
          </div>
        }
        <div class="ms-list">
          @for (o of shown(); track o.id) {
            <label class="ms-row"><input type="checkbox" [checked]="picked().has(o.id)" (change)="toggleOne(o.id)" /> {{ o.label }}</label>
          } @empty {
            <p class="muted small ms-empty">{{ options().length ? 'Nothing found.' : empty() }}</p>
          }
          @if (more()) { <p class="muted small ms-empty">{{ more() }} more. Type to narrow the list.</p> }
        </div>
      </div>
    }
  `,
})
export class MultiSelectComponent<T = number> {
  options = input.required<Option<T>[]>();
  picked = input.required<Set<T>>();
  placeholder = input('Pick...');
  empty = input('Nothing to pick yet.');
  pickedChange = output<Set<T>>();

  open = signal(false);
  query = signal('');
  private host = inject(ElementRef);

  @ViewChild('search') set search(el: ElementRef<HTMLInputElement> | undefined) {
    el?.nativeElement.focus();
  }

  matches = computed(() => filterOptions(this.options(), this.query()));
  shown = computed(() => this.matches().slice(0, MAX_SHOWN));
  more = computed(() => Math.max(0, this.matches().length - MAX_SHOWN));

  text = computed(() => {
    const names = this.options().filter((o) => this.picked().has(o.id)).map((o) => o.label);
    if (!names.length) return this.placeholder();
    if (names.length === this.options().length && names.length > 3) return `All (${names.length})`;
    return names.length > 3 ? `${names.slice(0, 3).join(', ')} +${names.length - 3} more` : names.join(', ');
  });

  toggle(): void {
    this.query.set('');
    this.open.set(!this.open());
  }

  toggleOne(id: T): void {
    const next = new Set(this.picked());
    next.has(id) ? next.delete(id) : next.add(id);
    this.pickedChange.emit(next);
  }

  setAll(on: boolean): void {
    const next = new Set(this.picked());
    for (const o of this.matches()) on ? next.add(o.id) : next.delete(o.id);
    this.pickedChange.emit(next);
  }

  outside(e: Event): void {
    if (this.open() && !this.host.nativeElement.contains(e.target)) this.open.set(false);
  }
}
