/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans:    ['var(--font-inter)', 'system-ui', 'sans-serif'],
        display: ['var(--font-inter)', 'system-ui', 'sans-serif'],
        mono:    ['var(--font-mono)', 'JetBrains Mono', 'monospace'],
      },
      colors: {
        /* shadcn/ui semantic tokens */
        border:      'hsl(var(--border))',
        input:       'hsl(var(--input))',
        ring:        'hsl(var(--ring))',
        background:  'hsl(var(--background))',
        foreground:  'hsl(var(--foreground))',
        primary:   { DEFAULT: 'hsl(var(--primary))', foreground: 'hsl(var(--primary-foreground))' },
        secondary: { DEFAULT: 'hsl(var(--secondary))', foreground: 'hsl(var(--secondary-foreground))' },
        destructive:{ DEFAULT: 'hsl(var(--destructive))', foreground: 'hsl(var(--destructive-foreground))' },
        muted:     { DEFAULT: 'hsl(var(--muted))', foreground: 'hsl(var(--muted-foreground))' },
        accent:    { DEFAULT: 'hsl(var(--accent))', foreground: 'hsl(var(--accent-foreground))' },
        card:      { DEFAULT: 'hsl(var(--card))', foreground: 'hsl(var(--card-foreground))' },
        popover:   { DEFAULT: 'hsl(var(--popover))', foreground: 'hsl(var(--popover-foreground))' },
        /* project tokens — SINGLE SOURCE OF TRUTH: styles/globals.css :root.
           Never hardcode hex here; reference the CSS vars so config + CSS + JS
           can never diverge again. */
        surface:   'var(--surface)',
        'surface-h': 'var(--surface-h)',
        bg:        'var(--bg)',
        line:      'var(--line)',
        violet:    { DEFAULT: 'var(--violet)', hover: 'var(--violet-h)', dim: 'var(--violet-dim)' },
        danger:    'var(--danger)',
        success:   'var(--success)',
        warning:   'var(--warning)',
      },
      borderRadius: {
        DEFAULT: '8px',
        none:    '0',
        sm:      '4px',
        md:      '8px',
        lg:      '8px',
        xl:      '8px',
        '2xl':   '8px',
        '3xl':   '8px',
        full:    '9999px',
      },
      boxShadow: {
        none:        'none',
        sm:          'none',
        md:          'none',
        lg:          'none',
        stripe:      'none',
        'stripe-lg': 'none',
        'stripe-btn':'none',
        violet:      'none',
        glass:       'none',
      },
      animation: {
        'fade-in':        'fadeIn 0.3s ease-out both',
        'slide-up':       'slideUp 0.4s ease-out both',
        'float':          'float 7s ease-in-out infinite',
        'blur-fade-in':   'fadeIn 0.4s ease-out both',
        'number-tick':    'countUp 0.4s ease-out both',
        'shimmer':        'skeletonShimmer 1.8s ease-in-out infinite',
        'accordion-down': 'accordionDown 0.2s ease-out',
        'accordion-up':   'accordionUp 0.2s ease-out',
        'spin-slow':      'spin 3s linear infinite',
      },
      keyframes: {
        fadeIn:  { from: { opacity: '0' }, to: { opacity: '1' } },
        slideUp: { from: { opacity: '0', transform: 'translateY(12px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
        float:   { '0%,100%': { transform: 'translateY(0)' }, '50%': { transform: 'translateY(-10px)' } },
        countUp: { from: { opacity: '0', transform: 'translateY(6px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
        accordionDown: { from: { height: '0' }, to: { height: 'var(--radix-accordion-content-height, auto)' } },
        accordionUp:   { from: { height: 'var(--radix-accordion-content-height, auto)' }, to: { height: '0' } },
      },
    },
  },
  plugins: [],
}
