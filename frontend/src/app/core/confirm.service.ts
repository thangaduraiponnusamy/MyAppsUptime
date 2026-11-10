import { Injectable, signal } from '@angular/core';

export interface ConfirmOptions {
  title: string;
  message: string;
  details?: string[];
  /** Text the user must type before the action button is enabled. */
  typeToConfirm?: string;
  action?: string;
}

interface OpenConfirm extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

@Injectable({ providedIn: 'root' })
export class ConfirmService {
  readonly current = signal<OpenConfirm | null>(null);

  ask(options: ConfirmOptions): Promise<boolean> {
    this.current()?.resolve(false);
    return new Promise((resolve) => this.current.set({ ...options, resolve }));
  }

  close(ok: boolean): void {
    const open = this.current();
    this.current.set(null);
    open?.resolve(ok);
  }
}
