import { Component, computed, inject, input } from '@angular/core';
import { Router } from '@angular/router';
import { Monitor } from '../core/models';
import { short } from './time';

interface MapNode { key: number; m: Monitor | null; x: number; y: number; depth: number; dim: boolean; kids: MapNode[]; }

const COL = 170;
const ROW = 46;
const PAD = 34;
const LABEL = 190;
const X0 = 60;

// Dependency tree (parent -> children), drawn left to right like a network status map
@Component({
  selector: 'app-status-map',
  standalone: true,
  template: `
    <div class="smap">
      @if (filter() !== 'all' && !layout().matched) {
        <div class="smap-empty">No monitors are {{ filter() }} right now.</div>
      }
      <svg [attr.width]="layout().width" [attr.height]="layout().height" role="img" aria-label="Status map">
        @for (l of layout().links; track $index) {
          <path [attr.d]="l.d" [class]="'smap-link ' + l.cls" />
        }
        @for (n of layout().nodes; track n.key) {
          <g class="smap-node" [class.click]="!!n.m" [class.dim]="n.dim" [attr.transform]="'translate(' + n.x + ',' + n.y + ')'" (click)="open(n)">
            <title>{{ n.m ? n.m.name + ' - ' + cls(n.m) + since(n.m) : 'All monitors' }}</title>
            @if (n.m) {
              <circle r="14" [class]="'halo ' + cls(n.m)" />
              <circle r="8" [class]="'core ' + cls(n.m)" />
              <text x="16" y="-2" class="name">{{ label(n.m.name) }}</text>
              <text x="16" y="12" class="meta">{{ cls(n.m) }}{{ since(n.m) }}</text>
            } @else {
              <circle r="15" class="root" />
              <path d="M-6 -5h12M-6 0h12M-6 5h12" class="root-ico" />
              <text x="0" y="32" text-anchor="middle" class="name">{{ title() }}</text>
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
  private router = inject(Router);

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
    const build = (m: Monitor | null, depth: number): MapNode => {
      if (m) seen.add(m.id);
      maxDepth = Math.max(maxDepth, depth);
      const node: MapNode = { key: m?.id ?? 0, m, depth, dim: !!m && !hits.has(m.id), x: X0 + depth * COL, y: 0, kids: [] };
      node.kids = (kids.get(m?.id ?? null) ?? []).filter((c) => !seen.has(c.id)).map((c) => build(c, depth + 1));
      node.y = node.kids.length
        ? (node.kids[0].y + node.kids[node.kids.length - 1].y) / 2
        : PAD + row++ * ROW;
      return node;
    };
    const root = build(null, 0);

    const nodes: MapNode[] = [];
    const links: { d: string; cls: string }[] = [];
    const walk = (n: MapNode) => {
      nodes.push(n);
      for (const k of n.kids) {
        const mx = (n.x + k.x) / 2;
        links.push({ d: `M${n.x} ${n.y}C${mx} ${n.y},${mx} ${k.y},${k.x} ${k.y}`, cls: this.cls(k.m!) });
        walk(k);
      }
    };
    walk(root);
    return { nodes, links, matched: hits.size, width: X0 + maxDepth * COL + LABEL, height: Math.max(row, 1) * ROW + PAD + 10 };
  });

  cls(m: Monitor): string {
    if (m.status === 'down' && m.parent_id !== null && this.byId().get(m.parent_id)?.status === 'down') return 'blocked';
    return m.status;
  }

  since(m: Monitor): string {
    return m.status_since ? ` · ${short(m.status_since)}` : '';
  }

  label(name: string): string {
    return name.length > 22 ? name.slice(0, 21) + '…' : name;
  }

  open(n: MapNode): void {
    if (n.m) this.router.navigate(['/monitors', n.m.id]);
  }
}
