import { Component, ElementRef, ViewChild, computed, effect, inject, input, output, signal } from '@angular/core';
import { Router } from '@angular/router';
import { MAP_STYLE_KEY } from '../core/auth.service';
import { Monitor, Project } from '../core/models';
import { SelectComponent } from './select.component';
import { short } from './time';

interface Group { id: number; name: string; cls: string; meta: string }
interface MapNode {
  key: string; m: Monitor | null; g: Group | null; x: number; y: number; depth: number; dim: boolean; kids: MapNode[];
  rot?: number; flip?: boolean;
}
type MapStyle = 'tree' | 'circle';

const RING = 130;
// Arc length per service on the circle
const SLOT = 34;
const ZOOM_MIN = 0.2;
const ZOOM_MAX = 4;
const COL = 170;
const ROW = 46;
const PAD = 34;
const LABEL = 190;
const X0 = 60;
// Above this many services the map shows projects only; click a project to see its services
const MAX_EXPANDED = 40;
const RANK: Record<string, number> = { down: 0, blocked: 1, degraded: 2, pending: 3 };

// Root -> projects -> dependency tree (parent -> children), drawn left to right like a network status map
@Component({
  selector: 'app-status-map',
  standalone: true,
  imports: [SelectComponent],
  host: { '(document:fullscreenchange)': 'onFullscreen()' },
  template: `
    <div class="smap-bar">
      <app-select class="smap-style" label="Map style" [options]="styles" [value]="style()" (valueChange)="setStyle($event)" />
      <span class="smap-zoom">
        <button type="button" class="btn sm" (click)="zoomBy(1 / 1.25)" [disabled]="zoom() <= ZOOM_MIN" title="Zoom out" aria-label="Zoom out">&minus;</button>
        <button type="button" class="btn sm pct" (click)="setZoom(1)" title="Actual size">{{ round(zoom() * 100) }}%</button>
        <button type="button" class="btn sm" (click)="zoomBy(1.25)" [disabled]="zoom() >= ZOOM_MAX" title="Zoom in" aria-label="Zoom in">+</button>
      </span>
      <button type="button" class="btn sm" (click)="fit()" title="Fit the whole map in view">Fit</button>
      <button type="button" class="btn sm" (click)="toggleFull()">{{ full() ? 'Exit full screen' : 'Full screen' }}</button>
    </div>
    <div #box class="smap" [class.circle]="style() === 'circle'" [class.dragging]="dragging" (wheel)="wheel($event)" (pointerdown)="down($event)"
      (pointermove)="move($event)" (pointerup)="drag = null" (pointerleave)="drag = null">
      @if (filter() !== 'all' && !layout().matched) {
        <div class="smap-empty">No monitors are {{ filter() }} right now.</div>
      }
      <svg [attr.width]="layout().width * zoom()" [attr.height]="layout().height * zoom()"
        [attr.viewBox]="'0 0 ' + layout().width + ' ' + layout().height" role="img" aria-label="Status map">
        @for (l of layout().links; track $index) {
          <path [attr.d]="l.d" [class]="'smap-link ' + l.cls" />
        }
        @for (n of layout().nodes; track n.key) {
          <g class="smap-node" [class.click]="!!n.m || !!n.g" [class.dim]="n.dim" [attr.transform]="'translate(' + n.x + ',' + n.y + ')'" (click)="open(n)">
            <title>{{ n.m ? n.m.name + ' - ' + cls(n.m) + since(n.m) : n.g ? 'Project ' + n.g.name + ' - ' + n.g.meta : title() }}</title>
            @if (n.g) {
              <circle r="16" [class]="'halo ' + n.g.cls" />
              <rect x="-9" y="-9" width="18" height="18" rx="5" [class]="'core ' + n.g.cls" />
              <g [attr.transform]="n.rot ? 'rotate(' + n.rot + ')' : null" [attr.text-anchor]="n.flip ? 'end' : null">
                <text [attr.x]="n.flip ? -18 : 18" y="-2" class="name">{{ label(n.g.name) }}</text>
                <text [attr.x]="n.flip ? -18 : 18" y="12" class="meta plain">{{ n.g.meta }}</text>
              </g>
            } @else if (n.m) {
              <circle r="14" [class]="'halo ' + cls(n.m)" />
              <circle r="8" [class]="'core ' + cls(n.m)" />
              <g [attr.transform]="n.rot ? 'rotate(' + n.rot + ')' : null" [attr.text-anchor]="n.flip ? 'end' : null">
                <text [attr.x]="n.flip ? -16 : 16" y="-2" class="name">{{ label(n.m.name) }}</text>
                <text [attr.x]="n.flip ? -16 : 16" y="12" class="meta">{{ cls(n.m) }}{{ since(n.m) }}</text>
              </g>
            } @else {
              <circle r="15" class="root" />
              <path d="M-6 -5h12M-6 0h12M-6 5h12" class="root-ico" />
              <text x="0" y="34" text-anchor="middle" class="name root-name">{{ title() }}</text>
            }
          </g>
        }
      </svg>
    </div>
  `,
})
export class StatusMapComponent {
  monitors = input.required<Monitor[]>();
  // Status key from the dashboard ('all', 'up', 'down', 'blocked', ...)
  filter = input('all');
  title = input('All monitors');
  /** When set, monitors are grouped under their project */
  projects = input<Project[]>([]);
  pickProject = output<number>();
  private router = inject(Router);
  private host = inject(ElementRef<HTMLElement>);
  @ViewChild('box') private box?: ElementRef<HTMLDivElement>;

  readonly ZOOM_MIN = ZOOM_MIN;
  readonly ZOOM_MAX = ZOOM_MAX;
  readonly round = Math.round;
  readonly styles = [{ id: 'tree' as MapStyle, label: 'Tree' }, { id: 'circle' as MapStyle, label: 'Circle' }];
  style = signal<MapStyle>(localStorage.getItem(MAP_STYLE_KEY) === 'circle' ? 'circle' : 'tree');
  zoom = signal(1);
  full = signal(false);
  drag: { x: number; y: number; left: number; top: number } | null = null;
  dragging = false;
  private moved = false;
  private fittedWidth = 0;

  constructor() {
    // Circle maps are big: fit them when they first show or their size changes, not on every refresh
    effect(() => {
      const { width } = this.layout();
      if (this.style() === 'circle' && width !== this.fittedWidth) setTimeout(() => this.fit());
      this.fittedWidth = width;
    });
  }

  private byId = computed(() => new Map(this.monitors().map((m) => [m.id, m])));

  layout = computed(() => {
    const ids = this.byId();
    const f = this.filter();
    // Matching monitors plus their ancestors (shown dimmed) so the tree keeps its shape
    const hits = new Set(this.monitors().filter((m) => f === 'all' || this.cls(m) === f).map((m) => m.id));
    const keep = new Set(hits);
    for (const id of hits) {
      let p = ids.get(id)?.parent_id ?? null;
      while (p !== null && ids.has(p) && !keep.has(p)) {
        keep.add(p);
        p = ids.get(p)!.parent_id;
      }
    }
    if (this.style() === 'circle') return this.circle(hits);
    const list = this.monitors().filter((m) => keep.has(m.id));
    const kids = new Map<number | null, Monitor[]>();
    for (const m of list) {
      const p = m.parent_id !== null && keep.has(m.parent_id) && m.parent_id !== m.id ? m.parent_id : null;
      kids.set(p, [...(kids.get(p) ?? []), m]);
    }
    for (const arr of kids.values()) arr.sort((a, b) => a.name.localeCompare(b.name));

    let row = 0;
    let maxDepth = 0;
    const seen = new Set<number>();
    const place = (node: MapNode) => {
      maxDepth = Math.max(maxDepth, node.depth);
      node.y = node.kids.length
        ? (node.kids[0].y + node.kids[node.kids.length - 1].y) / 2
        : PAD + row++ * ROW;
      return node;
    };
    const build = (m: Monitor, depth: number): MapNode => {
      seen.add(m.id);
      const node: MapNode = { key: 'm' + m.id, m, g: null, depth, dim: !hits.has(m.id), x: X0 + depth * COL, y: 0, kids: [] };
      node.kids = (kids.get(m.id) ?? []).filter((c) => !seen.has(c.id)).map((c) => build(c, depth + 1));
      return place(node);
    };
    const top = kids.get(null) ?? [];
    const root: MapNode = { key: 'root', m: null, g: null, depth: 0, dim: false, x: X0, y: 0, kids: [] };
    if (this.projects().length) {
      // One node per project, colored by its worst service; problems first
      const collapse = list.length > MAX_EXPANDED;
      const groups = this.projects()
        .map((p) => ({ p, all: this.monitors().filter((m) => (m.project_id ?? 0) === p.id) }))
        .filter((x) => x.all.length)
        .map((x) => ({ ...x, g: this.group(x.p, x.all) }))
        .sort((a, b) => (RANK[a.g.cls] ?? 9) - (RANK[b.g.cls] ?? 9) || a.p.name.localeCompare(b.p.name));
      for (const { p, all, g } of groups) {
        const items = top.filter((m) => (m.project_id ?? 0) === p.id);
        if (!items.length) continue;
        const node: MapNode = { key: 'p' + p.id, m: null, g, depth: 1, dim: !all.some((m) => hits.has(m.id)),
          x: X0 + COL, y: 0, kids: [] };
        if (collapse) items.forEach((m) => seen.add(m.id));
        else node.kids = items.filter((c) => !seen.has(c.id)).map((c) => build(c, 2));
        root.kids.push(place(node));
      }
      root.kids.push(...top.filter((m) => !seen.has(m.id)).map((m) => build(m, 1)));
    } else {
      root.kids = top.map((m) => build(m, 1));
    }
    place(root);

    const nodes: MapNode[] = [];
    const edges: [MapNode, MapNode][] = [];
    const walk = (n: MapNode) => {
      nodes.push(n);
      for (const k of n.kids) {
        edges.push([n, k]);
        walk(k);
      }
    };
    walk(root);
    const linkCls = (k: MapNode) => (k.g ? k.g.cls : this.cls(k.m!));
    const links = edges.map(([n, k]) => {
      const mx = (n.x + k.x) / 2;
      return { d: `M${n.x} ${n.y}C${mx} ${n.y},${mx} ${k.y},${k.x} ${k.y}`, cls: linkCls(k) };
    });
    return { nodes, links, matched: hits.size, width: X0 + maxDepth * COL + LABEL, height: Math.max(row, 1) * ROW + PAD + 10 };
  });

  /** Every matching service on one ring around the center, problems first */
  private circle(hits: Set<number>) {
    const list = this.monitors()
      .filter((m) => hits.has(m.id))
      .sort((a, b) => (RANK[this.cls(a)] ?? 9) - (RANK[this.cls(b)] ?? 9) || a.name.localeCompare(b.name));
    // Few services: a wider ring with level labels; many: labels point outward so they do not overlap
    const level = list.length <= 12;
    const r = Math.max(RING, (list.length * (level ? 140 : SLOT)) / (2 * Math.PI));
    const c = r + LABEL;
    const nodes: MapNode[] = [{ key: 'root', m: null, g: null, depth: 0, dim: false, x: c, y: c, kids: [] }];
    const links = [{ d: `M${c - r} ${c}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`, cls: 'ring' }];
    list.forEach((m, i) => {
      const a = (i / list.length) * 2 * Math.PI - Math.PI / 2;
      const flip = Math.cos(a) < -1e-6;
      const x = c + r * Math.cos(a);
      const y = c + r * Math.sin(a);
      nodes.push({ key: 'm' + m.id, m, g: null, depth: 1, dim: false, x, y, kids: [], flip, rot: level ? 0 : (a * 180) / Math.PI + (flip ? 180 : 0) });
      links.push({ d: `M${c} ${c}L${x} ${y}`, cls: this.cls(m) });
    });
    return { nodes, links, matched: hits.size, width: 2 * c, height: 2 * c };
  }

  setStyle(v: MapStyle | null): void {
    const s = v ?? 'tree';
    this.style.set(s);
    localStorage.setItem(MAP_STYLE_KEY, s);
    if (s === 'tree') this.zoom.set(1);
  }

  setZoom(z: number, anchor?: { x: number; y: number }): void {
    const el = this.box?.nativeElement;
    const old = this.zoom();
    const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, z));
    if (!el || next === old) return;
    // Keep the point under the cursor (or the center) in place
    const ax = anchor?.x ?? el.clientWidth / 2;
    const ay = anchor?.y ?? el.clientHeight / 2;
    const cx = (el.scrollLeft + ax) / old;
    const cy = (el.scrollTop + ay) / old;
    this.zoom.set(next);
    setTimeout(() => el.scrollTo(cx * next - ax, cy * next - ay));
  }

  zoomBy(f: number): void {
    this.setZoom(this.zoom() * f);
  }

  fit(): void {
    const el = this.box?.nativeElement;
    if (!el) return;
    const { width, height } = this.layout();
    // The box shrinks with the map, so do not let its current height force a tiny zoom
    const h = this.full() ? el.clientHeight : Math.max(el.clientHeight, 520);
    const z = Math.min(this.full() ? 2 : 1, Math.max(ZOOM_MIN, Math.min((el.clientWidth - 4) / width, (h - 4) / height)));
    this.zoom.set(z);
    setTimeout(() => el.scrollTo(0, 0));
  }

  wheel(e: WheelEvent): void {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    const r = this.box!.nativeElement.getBoundingClientRect();
    this.setZoom(this.zoom() * (e.deltaY < 0 ? 1.15 : 1 / 1.15), { x: e.clientX - r.left, y: e.clientY - r.top });
  }

  down(e: PointerEvent): void {
    if (e.button !== 0 || e.pointerType === 'touch') return;
    const el = this.box!.nativeElement;
    this.drag = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop };
    this.moved = false;
    this.dragging = false;
  }

  move(e: PointerEvent): void {
    if (!this.drag) return;
    const dx = e.clientX - this.drag.x;
    const dy = e.clientY - this.drag.y;
    if (!this.moved && Math.abs(dx) + Math.abs(dy) < 5) return;
    this.moved = this.dragging = true;
    this.box!.nativeElement.scrollTo(this.drag.left - dx, this.drag.top - dy);
  }

  toggleFull(): void {
    if (document.fullscreenElement) document.exitFullscreen();
    else this.host.nativeElement.requestFullscreen?.();
  }

  onFullscreen(): void {
    this.full.set(document.fullscreenElement === this.host.nativeElement);
    this.dragging = false;
    setTimeout(() => this.fit(), 50);
  }

  cls(m: Monitor): string {
    if (m.status === 'down' && m.parent_id !== null && this.byId().get(m.parent_id)?.status === 'down') return 'blocked';
    return m.status;
  }

  /** Worst status of the project's services; "Up · 5d" = all services up for that long */
  private group(p: Project, all: Monitor[]): Group {
    const live = all.filter((m) => m.status !== 'paused');
    const down = live.filter((m) => m.status === 'down');
    const degraded = live.filter((m) => m.status === 'degraded');
    const cls = down.length ? 'down' : degraded.length ? 'degraded' : live.some((m) => m.status === 'up') ? 'up' : live.length ? live[0].status : 'paused';
    const times = (list: Monitor[]) => list.map((m) => m.status_since).filter((t): t is string => !!t).sort();
    let meta: string;
    if (cls === 'up') {
      const t = times(live).pop();
      meta = `${live.length} up${t ? ' · ' + short(t) : ''}`;
    } else if (down.length || degraded.length) {
      const bad = down.length ? down : degraded;
      const t = times(bad)[0];
      meta = `${bad.length} of ${live.length} ${cls}${t ? ' · ' + short(t) : ''}`;
    } else {
      meta = cls.charAt(0).toUpperCase() + cls.slice(1);
    }
    return { id: p.id!, name: p.name, cls, meta };
  }

  since(m: Monitor): string {
    return m.status_since ? ` · ${short(m.status_since)}` : '';
  }

  label(name: string): string {
    return name.length > 22 ? name.slice(0, 21) + '…' : name;
  }

  open(n: MapNode): void {
    if (this.moved) return;
    if (n.m) this.router.navigate(['/monitors', n.m.id]);
    else if (n.g) this.pickProject.emit(n.g.id);
  }
}
