import { Component, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ThemeService } from './core/theme.service';
import { ConfirmDialogComponent } from './shared/confirm-dialog.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, ConfirmDialogComponent],
  template: `<router-outlet /><app-confirm-dialog />`,
})
export class AppComponent {
  // Instantiated here so the saved theme also applies to public status pages
  private theme = inject(ThemeService);
}
