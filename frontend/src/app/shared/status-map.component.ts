import { Component, computed, inject, input, output } from '@angular/core';
import { Router } from '@angular/router';
import { Monitor, Project } from '../core/models';
import { short } from './time';

interface Group { id: number; name: string; cls: string; meta: string }
interface MapNode { key: string; m: Monitor | null; g: Group | null; x: number; y: number; depth: number; dim: boolean; kids: MapNode[]; }

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
          <g class="smap-node" [class.click]="!!n.m || !!n.g" [class.dim]="n.dim" [attr.transform]="'translate(' + n.x + ',' + n.y + ')'" (click)="open(n)">
            <title>{{ n.m ? n.m.name + ' - ' + cls(n.m) + since(n.m) : n.g ? 'Project ' + n.g.name + ' - ' + n.g.meta : title() }}</title>
            @if (n.g) {
              <circle r="16" [class]="'halo ' + n.g.cls" />
              <rect x="-9" y="-9" width="18" height="18" rx="5" [class]="'core ' + n.g.cls" />
              <text x="18" y="-2" class="name">{{ label(n.g.name) }}</text>
              <text x="18" y="12" class="meta plain">{{ n.g.meta }}</text>
            } @else if (n.m) {
              <circle r="14" [class]="'halo ' + cls(n.m)" />
              <circle r="8" [class]="'core ' + cls(n.m)" />
              <text x="16" y="-2" class="name">{{ label(n.m.name) }}</text>
              <text x="16" y="12" class="meta">{{ cls(n.m) }}{{ since(n.m) }}</text>
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
    const links: { d: string; cls: string }[] = [];
    const walk = (n: MapNode) => {
      nodes.push(n);
      for (const k of n.kids) {
        const mx = (n.x + k.x) / 2;
        links.push({ d: `M${n.x} ${n.y}C${mx} ${n.y},${mx} ${k.y},${k.x} ${k.y}`, cls: k.g ? k.g.cls : this.cls(k.m!) });
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
    if (n.m) this.router.navigate(['/monitors', n.m.id]);
    else if (n.g) this.pickProject.emit(n.g.id);
  }
}
