/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Legacy palette (kept for backward compat)
        ods: {
          bg: '#0f0f13',
          card: '#18181b',
          border: '#27272a'
        },
        // Theme-aware colors driven by CSS custom properties.
        // Values use rgb() with <alpha-value> so Tailwind's opacity-modifier
        // syntax (e.g. bg-theme-card/95) can inject the alpha channel.
        // The matching CSS vars in index.css store space-separated R G B
        // triplets (e.g. --theme-card: 24 24 27) instead of hex.
        theme: {
          bg: 'rgb(var(--theme-bg) / <alpha-value>)',
          card: 'rgb(var(--theme-card) / <alpha-value>)',
          border: 'rgb(var(--theme-border) / <alpha-value>)',
          text: 'rgb(var(--theme-text) / <alpha-value>)',
          'text-secondary': 'rgb(var(--theme-text-secondary) / <alpha-value>)',
          'text-muted': 'rgb(var(--theme-text-muted) / <alpha-value>)',
          accent: 'rgb(var(--theme-accent) / <alpha-value>)',
          'accent-hover': 'rgb(var(--theme-accent-hover) / <alpha-value>)',
          'accent-light': 'rgb(var(--theme-accent-light) / <alpha-value>)',
          'surface-hover': 'rgb(var(--theme-surface-hover) / <alpha-value>)',
          sidebar: 'rgb(var(--theme-sidebar) / <alpha-value>)',
        }
      },
      animation: {
        shimmer: 'shimmer 2s linear infinite',
      },
      keyframes: {
        shimmer: {
          '0%': { transform: 'translateX(-100%)' },
          '100%': { transform: 'translateX(100%)' },
        }
      }
    },
  },
  plugins: [],
}
