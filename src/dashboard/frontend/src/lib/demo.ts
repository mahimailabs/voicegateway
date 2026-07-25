// Build-time demo flag.
//
// Set only by `npm run build:demo` (`vite build --mode demo`). Vite statically
// replaces `import.meta.env.MODE` at build time, so in the NORMAL build this
// folds to the literal `false` and rollup dead-code-eliminates every
// `if (DEMO_MODE)` branch. The demo fixtures are loaded via a dynamic import()
// that lives only inside such a branch, so the real dashboard bundle ships none
// of the demo data or code.
//
// When on, the dashboard runs the real UI against seeded static fixtures with no
// backend: a no-login, read-only "command center" for voicegateway.dev/demo.
export const DEMO_MODE: boolean = import.meta.env.MODE === 'demo';
