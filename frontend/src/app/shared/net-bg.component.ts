import { AfterViewInit, Component, ElementRef, NgZone, OnDestroy, effect, inject, viewChild } from '@angular/core';
import { ThemeService } from '../core/theme.service';

interface Dot { x: number; y: number; vx: number; vy: number; r: number; glow: boolean; phase: number; }
interface Spark { a: Dot; b: Dot; p: number; speed: number; c: string; }

const SPARKS = 7;

const LINK = 150;
const FPS = 30;

// Faint drifting network (dots + lines) behind the whole app; cards sit on top of it
@Component({
  selector: 'app-net-bg',
  standalone: true,
  template: `<canvas #c class="net-bg" aria-hidden="true"></canvas>`,
})
export class NetBgComponent implements AfterViewInit, OnDestroy {
  private canvas = viewChild.required<ElementRef<HTMLCanvasElement>>('c');
  private zone = inject(NgZone);
  private theme = inject(ThemeService);
  private dots: Dot[] = [];
  private sparks: Spark[] = [];
  private w = 0;
  private h = 0;
  private raf = 0;
  private last = 0;
  private still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  private onResize = () => this.resize();

  constructor() {
    effect(() => {
      this.theme.theme();
      if (this.still && this.w) this.draw(0);
    });
  }

  ngAfterViewInit(): void {
    this.zone.runOutsideAngular(() => {
      addEventListener('resize', this.onResize);
      this.resize();
      if (!this.still) this.raf = requestAnimationFrame((t) => this.tick(t));
    });
  }

  ngOnDestroy(): void {
    cancelAnimationFrame(this.raf);
    removeEventListener('resize', this.onResize);
  }

  private resize(): void {
    // Phones fire resize when the address bar shows or hides; rebuilding then causes a visible stall
    if (this.w && innerWidth === this.w && Math.abs(innerHeight - this.h) < 160) return;
    const c = this.canvas().nativeElement;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    this.w = innerWidth;
    this.h = innerHeight;
    c.width = this.w * dpr;
    c.height = this.h * dpr;
    c.getContext('2d')!.setTransform(dpr, 0, 0, dpr, 0, 0);
    const n = Math.min(90, Math.round((this.w * this.h) / 16000));
    this.dots = Array.from({ length: n }, () => ({
      x: Math.random() * this.w, y: Math.random() * this.h,
      vx: (Math.random() - 0.5) * 0.25, vy: (Math.random() - 0.5) * 0.25,
      r: 0.7 + Math.random() * 1, glow: Math.random() < 0.18, phase: Math.random() * Math.PI * 2,
    }));
    this.draw(0);
  }

  private tick(t: number): void {
    this.raf = requestAnimationFrame((n) => this.tick(n));
    if (document.hidden || t - this.last < 1000 / FPS) return;
    this.last = t;
    for (const d of this.dots) {
      d.x += d.vx;
      d.y += d.vy;
      if (d.x < -20) d.x = this.w + 20; else if (d.x > this.w + 20) d.x = -20;
      if (d.y < -20) d.y = this.h + 20; else if (d.y > this.h + 20) d.y = -20;
    }
    for (const s of this.sparks) s.p += s.speed;
    this.sparks = this.sparks.filter((s) => s.p < 1 && Math.hypot(s.a.x - s.b.x, s.a.y - s.b.y) < LINK);
    if (this.sparks.length < SPARKS && Math.random() < 0.15) this.spawn();
    this.draw(t);
  }

  private spawn(): void {
    const a = this.dots[Math.floor(Math.random() * this.dots.length)];
    const near = this.dots.filter((b) => b !== a && Math.hypot(a.x - b.x, a.y - b.y) < LINK);
    if (!near.length) return;
    const b = near[Math.floor(Math.random() * near.length)];
    this.sparks.push({ a, b, p: 0, speed: 0.012 + Math.random() * 0.012, c: Math.random() < 0.5 ? '6, 182, 212' : '99, 102, 241' });
  }

  private draw(t: number): void {
    const ctx = this.canvas().nativeElement.getContext('2d')!;
    const dark = this.theme.theme() === 'dark';
    const rgb = dark ? '148, 163, 184' : '71, 85, 105';
    const base = dark ? 0.22 : 0.16;
    // Stronger towards the left/right edges, lighter in the middle where content is
    const edge = (x: number) => 0.2 + 0.8 * Math.min(1, Math.abs(x - this.w / 2) / (this.w / 2) * 1.25);
    ctx.clearRect(0, 0, this.w, this.h);
    ctx.lineWidth = 0.8;
    for (let i = 0; i < this.dots.length; i++) {
      const a = this.dots[i];
      for (let j = i + 1; j < this.dots.length; j++) {
        const b = this.dots[j];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        if (dist > LINK) continue;
        ctx.strokeStyle = `rgba(${rgb}, ${(1 - dist / LINK) * base * edge((a.x + b.x) / 2)})`;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }
    for (const d of this.dots) {
      const k = edge(d.x);
      if (!d.glow) {
        ctx.fillStyle = `rgba(${rgb}, ${base * 2 * k})`;
        ctx.beginPath();
        ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
        ctx.fill();
        continue;
      }
      const pulse = 0.5 + 0.5 * Math.sin(t / 500 + d.phase);
      this.light(ctx, d.x, d.y, d.phase > Math.PI ? '6, 182, 212' : '99, 102, 241', 0.9, (0.35 + pulse * 0.65) * k);
    }
    for (const s of this.sparks) {
      const x = s.a.x + (s.b.x - s.a.x) * s.p;
      const y = s.a.y + (s.b.y - s.a.y) * s.p;
      this.light(ctx, x, y, s.c, 1.1, Math.sin(Math.PI * s.p) * edge(x));
    }
  }

  // Small bright point with a tight glow, like a spark
  private light(ctx: CanvasRenderingContext2D, x: number, y: number, c: string, r: number, a: number): void {
    const halo = r * 3;
    const g = ctx.createRadialGradient(x, y, 0, x, y, halo);
    g.addColorStop(0, `rgba(${c}, ${a})`);
    g.addColorStop(0.3, `rgba(${c}, ${a * 0.8})`);
    g.addColorStop(1, `rgba(${c}, 0)`);
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(x, y, halo, 0, Math.PI * 2);
    ctx.fill();
  }
}
