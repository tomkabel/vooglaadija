# Frontend Development Guide

This document describes the frontend architecture, build process, and development workflow for the
Vooglaadija project.

## Architecture Overview

The frontend uses a modern stack:

- **HTMX 2.0** - For interactive components without JavaScript
- **Tailwind CSS 3.4** - For utility-first styling with custom design system
- **Jinja2 Templates** - Server-rendered HTML with Python backend
- **SSE (Server-Sent Events)** - For real-time updates

## Project Structure

```text
frontend/
├── css/
│   ├── src/styles.css        # Tailwind source with custom components
│   └── dist/styles.css       # Built CSS output (minified)
├── tailwind.config.js        # Tailwind configuration
├── postcss.config.js         # PostCSS configuration
├── .browserslistrc           # Browser support targets
├── package.json              # NPM scripts and dependencies
└── (static assets planned for future)

app/
├── static/
│   └── css/styles.css        # Deployed CSS (copied from frontend)
└── templates/                # Jinja2 HTML templates
    ├── base.html             # Base layout template
    ├── dashboard.html        # Main dashboard
    ├── login.html            # Authentication
    ├── register.html         # Registration
    └── partials/             # Reusable components
```

## Development Workflow

### Prerequisites

- Node.js 18+ and pnpm installed
- Python 3.11+ with project dependencies

### Setup

1. Install frontend dependencies:

   ```bash
   cd frontend
   pnpm install
   ```

### Development Commands

| Command                 | Description                          |
| ----------------------- | ------------------------------------ |
| `pnpm run dev`          | Watch mode: rebuild CSS on changes   |
| `pnpm run build`        | Production build: minified CSS       |
| `pnpm run deploy`       | Build + copy to app static directory |
| `pnpm run watch:deploy` | Watch + auto-deploy (manual)         |

### CSS Development Process

1. Edit `frontend/css/src/styles.css`
1. Run `pnpm run dev` to watch for changes
1. Run `pnpm run deploy` to update app static CSS
1. Refresh browser to see changes

## Tailwind Configuration

### Custom Design System

The project uses a custom design system defined in `tailwind.config.js`:

#### Colors

- **Surface**: Dark theme backgrounds (`surface-900` to `surface-50`)
- **Amber**: Primary accent color (`amber-50` to `amber-950`)
- **Warm**: Secondary accent (`warm-50` to `warm-900`)
- **Coral**: Error/danger states (`coral-50` to `coral-900`)
- **Jade**: Success states (`jade-50` to `jade-900`)

#### Fonts

- **Display**: Outfit (headings)
- **Body**: DM Sans (body text)
- **Mono**: JetBrains Mono (code)

#### Animations

Custom keyframes and animations for:

- `fade-in`, `slide-up`, `slide-in-right`
- `glow-pulse`, `grain`, `float`

#### Components

Custom component classes defined in CSS:

- Cards: `.card`, `.card-glow`, `.stat-card`
- Buttons: `.btn-primary`, `.btn-secondary`, `.btn-ghost`, `.btn-danger`, `.btn-download`
- Forms: `.form-input`, `.form-label`, `.input-icon-wrapper`
- Status: `.status-badge`, `.status-*` variants
- Navigation: `.nav-glass`
- Visual effects: `.text-gradient`, `.grain-overlay`, `.ambient-glow`

## Template Guidelines

### Class Usage

- Use custom component classes when available (`.btn-primary`, `.card`)
- Use Tailwind utilities for layout and spacing (`flex`, `gap-3`, `p-4`)
- Use custom colors (`bg-surface-900`, `text-amber-400`)
- Use responsive prefixes (`sm:`, `md:`, `lg:`)

### Animation Patterns

- Use `slide-up`, `fade-in` for entry animations
- Use `animate-stagger-*` for sequenced animations
- Use `opacity-0-initial` + `animate-fill-forwards` instead of inline styles

### HTMX Integration

- Use `hx-post`, `hx-get`, `hx-delete` for AJAX requests
- Use `hx-target` and `hx-swap` for DOM updates
- Use SSE for real-time updates (`sse-connect`, `sse-swap`)

## CSS Organization

The CSS source is organized into layers:

### Base Layer (`@layer base`)

- Global styles, scrollbar, selection colors
- Font and color defaults

### Components Layer (`@layer components`)

- Custom component classes grouped by function
- Each section has clear comments

### Utilities Layer (`@layer utilities`)

- Custom utility classes for specific needs
- Animation helpers, 3D transforms

## Browser Support

Targets modern browsers (last 2 versions, not IE 11). See `.browserslistrc` for details.

## Performance Considerations

- Built CSS is minified (~26KB)
- Tailwind uses JIT (Just-In-Time) compilation
- Unused classes are automatically purged
- Custom colors reduce CSS variable overhead

## Troubleshooting

### CSS Not Updating

1. Run `pnpm run deploy` to rebuild and copy
1. Clear browser cache
1. Check browser console for errors

### Build Errors

1. Check `frontend/css/src/styles.css` for syntax errors
1. Ensure Tailwind config paths are correct
1. Update browserslist DB: `npx update-browserslist-db@latest`

### Missing Classes

1. Check if class is defined in CSS source
1. Check if class is in Tailwind config safelist
1. Rebuild CSS with `pnpm run deploy`

## Future Enhancements

1. Add PostCSS autoprefixer for better browser compatibility
1. Add CSS minification plugin for further optimization
1. Consider adding Tailwind plugins (forms, typography)
1. Add dark mode support if needed
1. Automate CSS deployment in CI/CD pipeline
