import { Component, input } from '@angular/core';

let nextId = 0;

@Component({
  selector: 'app-logo',
  standalone: true,
  template: `
    <svg class="logo-mark" [attr.width]="size()" [attr.height]="size()" viewBox="0 0 64 64" role="img" aria-label="MyAppsUptime">
      <defs>
        <linearGradient [attr.id]="gid" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
          <stop offset="0" stop-color="#4f46e5" />
          <stop offset="1" stop-color="#06b6d4" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" [attr.fill]="'url(#' + gid + ')'" />
      <path d="M0 16A16 16 0 0 1 16 0h32a16 16 0 0 1 16 16v6C44 30 20 30 0 22z" fill="#fff" opacity=".1" />
      <path d="M11 37h8l5.5-15 7.5 23 7.5-23 5.5 15h4" fill="none" stroke="#fff" stroke-width="5"
            stroke-linecap="round" stroke-linejoin="round" />
      <circle cx="52" cy="37" r="4" fill="#fff" />
    </svg>
    @if (wordmark()) {
      <span class="wordmark">
        <span class="name"><span class="my">My</span><span class="apps">Apps</span><span class="uptime">Uptime</span></span>
        @if (tagline()) { <span class="tagline">Your watcher</span> }
      </span>
    }
  `,
  styles: [':host { display: inline-flex; align-items: center; gap: 12px; }'],
})
export class LogoComponent {
  size = input(40);
  wordmark = input(true);
  tagline = input(true);
  // Unique gradient id so several logos can render on one page
  readonly gid = `mau-grad-${nextId++}`;
}
