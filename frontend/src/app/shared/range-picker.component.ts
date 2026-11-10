import { Component, computed, input, output, signal } from '@angular/core';
import { formatDate } from '@angular/common';
import { IconComponent } from './icon.component';

export interface DayRange { from: string; to: string; }
export const RANGES = [{ hours: 24, label: '24h' }, { hours: 168, label: '7d' }, { hours: 720, label: '30d' }];
export const DAY = /^\d{4}-\d{2}-\d{2}$/;

const fmt = (d: string | number) => formatDate(d, 'yyyy-MM-dd', 'en-US');

export function rangeLabel(hours: number, custom: DayRange | null): string {
  if (!custom) return RANGES.find((r) => r.hours === hours)?.label ?? '';
  const f = (d: string) => formatDate(d, 'MMM d', 'en-US');
  return custom.from === custom.to ? f(custom.from) : `${f(custom.from)} – ${f(custom.to)}`;
}

export function rangeQuery(hours: number, custom: DayRange | null): { hours?: number; start?: string; end?: string } {
  if (!custom) return { hours };
  return { start: new Date(`${custom.from}T00:00:00`).toISOString(), end: new Date(`${custom.to}T23:59:59`).toISOString() };
}

// 24h / 7d / 30d / Custom picker
@Component({
  selector: 'app-range-picker',
  standalone: true,
  imports: [IconComponent],
  template: `
    <div class="range-picker">
      <div class="segmented">
        @for (r of ranges; track r.hours) {
          <button class="chip" [class.active]="!custom() && hours() === r.hours" (click)="open.set(false); pick.emit(r.hours)">{{ r.label }}</button>
        }
        <button class="chip" [class.active]="!!custom()" (click)="toggle()">
          <app-icon name="calendar" [size]="14" /> {{ custom() ? label() : 'Custom' }}
        </button>
      </div>
      @if (open()) {
        <div class="range-pop card">
          <div class="range-fields">
            <label>From <input type="date" [value]="from()" [max]="to()" (input)="from.set($any($event.target).value)" /></label>
            <label>To <input type="date" [value]="to()" [min]="from()" [max]="today" (input)="to.set($any($event.target).value)" /></label>
          </div>
          @if (error(); as err) { <div class="range-error">{{ err }}</div> }
          <div class="range-actions">
            <button class="btn sm" (click)="open.set(false)">Cancel</button>
            <button class="btn sm primary" [disabled]="!!error()" (click)="apply()">Apply</button>
          </div>
        </div>
      }
    </div>
  `,
})
export class RangePickerComponent {
  hours = input(24);
  custom = input<DayRange | null>(null);
  pick = output<number>();
  range = output<DayRange>();

  readonly ranges = RANGES;
  readonly today = fmt(Date.now());
  open = signal(false);
  from = signal(fmt(Date.now() - 6 * 86_400_000));
  to = signal(this.today);
  label = computed(() => rangeLabel(this.hours(), this.custom()));
  error = computed(() => {
    const from = this.from(), to = this.to();
    if (!from || !to) return 'Pick both dates';
    if (from > to) return 'From must be before To';
    if ((Date.parse(to) - Date.parse(from)) / 86_400_000 >= 90) return 'Range can be at most 90 days';
    return null;
  });

  toggle(): void {
    const c = this.custom();
    if (c) { this.from.set(c.from); this.to.set(c.to); }
    this.open.set(!this.open());
  }

  apply(): void {
    if (this.error()) return;
    this.open.set(false);
    this.range.emit({ from: this.from(), to: this.to() });
  }
}
