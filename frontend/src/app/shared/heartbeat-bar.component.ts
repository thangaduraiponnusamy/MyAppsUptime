import { formatDate } from '@angular/common';
import { Component, computed, input, signal } from '@angular/core';
import { short } from './time';

export interface BeatDetail { time: string; message?: string | null; latency?: number | null; }

@Component({
  selector: 'app-heartbeat-bar',
  standalone: true,
  template: `
    <div class="hb-wrap" (mouseleave)="hover.set(-1)">
      <div class="hb-bar" [class.sm]="size() === 'sm'">
        @for (s of padded(); track $index) {
          <span [class]="'beat ' + s" [class.on]="hover() === $index" [title]="details().length ? '' : s"
            (mouseenter)="hover.set($index)"></span>
        }
      </div>
      @if (tip(); as t) {
        <div class="hb-tip" [style.left.%]="t.left">
          <b [class]="t.status">{{ t.status.toUpperCase() }}</b> <span>{{ t.time }}</span>
          @if (t.latency != null) { <span class="muted">{{ t.latency }} ms</span> }
          @if (t.message) { <div class="muted">{{ t.message }}</div> }
        </div>
      }
      @if (labels() && since(); as s) {
        <div class="hb-labels"><span>{{ s }}</span><span>now</span></div>
      }
    </div>
  `,
})
export class HeartbeatBarComponent {
  statuses = input<string[]>([]);
  details = input<BeatDetail[]>([]);
  max = input(50);
  size = input<'sm' | 'lg'>('lg');
  labels = input(false);
  hover = signal(-1);

  private pad = computed(() => this.max() - Math.min(this.statuses().length, this.max()));

  padded = computed(() => {
    const recent = this.statuses().slice(-this.max());
    return [...Array(this.pad()).fill('empty'), ...recent];
  });

  private shown = computed(() => this.details().slice(-this.max()));

  tip = computed(() => {
    const i = this.hover() - this.pad();
    const d = this.shown()[i];
    if (!d) return null;
    const left = Math.min(85, Math.max(15, ((this.hover() + 0.5) / this.max()) * 100));
    return {
      left, status: this.padded()[this.hover()], time: formatDate(d.time, 'yyyy-MM-dd HH:mm:ss', 'en-US'),
      latency: d.latency == null ? null : Math.round(d.latency), message: d.message,
    };
  });

  since = computed(() => {
    const first = this.shown()[0];
    return first ? short(first.time).split(' ')[0] + ' ago' : '';
  });
}
