# Dark Mode Styling Reference

All Renfield frontend components must support light and dark mode using Tailwind `dark:` variants.

## Standard Patterns

```jsx
// Page backgrounds
className="bg-gray-50 dark:bg-gray-900"

// Cards, modals, panels
className="bg-white dark:bg-gray-800"

// Primary text
className="text-gray-900 dark:text-white"

// Secondary text
className="text-gray-600 dark:text-gray-300"

// Muted text
className="text-gray-500 dark:text-gray-400"

// Borders
className="border-gray-200 dark:border-gray-700"

// Hover states
className="hover:bg-gray-100 dark:hover:bg-gray-700"

// Input fields
className="bg-white dark:bg-gray-700 border-gray-300 dark:border-gray-600 text-gray-900 dark:text-white"
```

## Component Classes

Defined in `src/frontend/src/index.css`:

| Class | Usage |
|-------|-------|
| `.card` | Card container — handles bg, border, shadow, dark mode |
| `.input` | Text input — handles bg, border, text color, dark mode |
| `.btn-primary` | Primary action button |
| `.btn-secondary` | Secondary action button |

Prefer these classes over manual Tailwind dark mode for consistency.

## Theme Context

`src/frontend/src/contexts/ThemeContext.jsx`

```jsx
import { useTheme } from '../contexts/ThemeContext';

const { theme, isDark, setTheme, toggleTheme } = useTheme();
// theme: 'light' | 'dark' | 'system'
// Persisted in localStorage as 'renfield_theme'
```

## Rules

- NEVER use hardcoded colors without a `dark:` variant
- ALWAYS test both light and dark mode
- Use component classes (`.card`, `.input`) when available
- For new patterns, follow the standard patterns above
