---
name: add-frontend-page
description: Guide for creating new frontend pages in Renfield. Covers page creation, routing, navigation, dark mode styling, i18n translations, and WebSocket integration. Triggers on "add page", "neue Seite", "Frontend-Seite erstellen", "add route", "neue Route", "add admin page".
---

# Adding a New Frontend Page

## Quick Start (3 Files)

### 1. Create page component

`src/frontend/src/pages/YourPage.jsx`

```jsx
import { useTranslation } from 'react-i18next';

export default function YourPage() {
  const { t } = useTranslation();

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold text-gray-900 dark:text-white mb-6">
        {t('yourFeature.title')}
      </h1>
      <div className="card">
        {/* Content */}
      </div>
    </div>
  );
}
```

### 2. Add route

`src/frontend/src/App.jsx` — add inside the `<Routes>`:

```jsx
<Route path="/your-page" element={<YourPage />} />
```

### 3. Add navigation

`src/frontend/src/components/Layout.jsx` — add to navigation array.

## Mandatory Rules

1. **Dark Mode** — ALL components must use Tailwind `dark:` variants. Never use hardcoded colors.
2. **i18n** — ALL user-facing strings must use `useTranslation()`. Never hardcode text.
3. **Translations** — Add keys to BOTH `de.json` and `en.json`.

## Component Classes

Reusable classes defined in `src/frontend/src/index.css`:
- `.card` — Card container with dark mode
- `.input` — Input field with dark mode
- `.btn-primary` — Primary button
- `.btn-secondary` — Secondary button

## See Also

- `references/dark-mode.md` — Complete dark mode patterns
- `references/i18n.md` — i18n patterns and locale files
- `references/websocket-protocol.md` — WebSocket integration
