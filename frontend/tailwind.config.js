/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Surface layers — terminal trading dark theme
        surface: {
          0: '#0a0a0b',   // app background
          1: '#111113',   // card/panel background
          2: '#18181c',   // elevated panel
          3: '#1f1f26',   // input/control background
          4: '#26262f',   // hover state
          5: '#2e2e38',   // active / selected
        },
        border: {
          subtle:  '#1e1e28',
          default: '#2a2a36',
          strong:  '#3a3a4a',
        },
        text: {
          primary:   '#e8e8f0',
          secondary: '#9898b0',
          muted:     '#5a5a72',
        },
        profit: '#22c55e',
        loss:   '#ef4444',
        accent: {
          blue:   '#3b82f6',
          amber:  '#f59e0b',
          purple: '#a855f7',
          teal:   '#14b8a6',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Cascadia Code', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      fontSize: {
        'num-xs': ['0.7rem', { lineHeight: '1rem', fontVariantNumeric: 'tabular-nums' }],
        'num-sm': ['0.8rem', { lineHeight: '1.125rem', fontVariantNumeric: 'tabular-nums' }],
        'num-base': ['0.875rem', { lineHeight: '1.25rem', fontVariantNumeric: 'tabular-nums' }],
        'num-lg': ['1rem', { lineHeight: '1.5rem', fontVariantNumeric: 'tabular-nums' }],
        'num-xl': ['1.25rem', { lineHeight: '1.75rem', fontVariantNumeric: 'tabular-nums' }],
      },
      keyframes: {
        'flash-up': {
          '0%':   { backgroundColor: 'rgba(34,197,94,0.35)' },
          '100%': { backgroundColor: 'transparent' },
        },
        'flash-down': {
          '0%':   { backgroundColor: 'rgba(239,68,68,0.35)' },
          '100%': { backgroundColor: 'transparent' },
        },
        'flash-neutral': {
          '0%':   { backgroundColor: 'rgba(148,163,184,0.2)' },
          '100%': { backgroundColor: 'transparent' },
        },
        'fade-in': {
          '0%':   { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-in': {
          '0%':   { transform: 'translateX(100%)' },
          '100%': { transform: 'translateX(0)' },
        },
        'pulse-dot': {
          '0%, 100%': { opacity: '1' },
          '50%':      { opacity: '0.3' },
        },
      },
      animation: {
        'flash-up':      'flash-up 0.6s ease-out forwards',
        'flash-down':    'flash-down 0.6s ease-out forwards',
        'flash-neutral': 'flash-neutral 0.4s ease-out forwards',
        'fade-in':       'fade-in 0.2s ease-out',
        'slide-in':      'slide-in 0.25s ease-out',
        'pulse-dot':     'pulse-dot 1.5s ease-in-out infinite',
      },
      boxShadow: {
        'panel':  '0 1px 3px rgba(0,0,0,0.4), 0 1px 2px rgba(0,0,0,0.6)',
        'modal':  '0 20px 60px rgba(0,0,0,0.7)',
        'glow-profit': '0 0 8px rgba(34,197,94,0.3)',
        'glow-loss':   '0 0 8px rgba(239,68,68,0.3)',
      },
    },
  },
  plugins: [],
}
