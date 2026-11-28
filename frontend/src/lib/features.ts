/**
 * Feature switches, set in frontend/.env.local (restart `npm run dev` after changing).
 *
 * Reports are off by default while we test the conversation: each one is a
 * paid call to the report model. Turn them back on with VITE_ENABLE_REPORTS=true.
 */
export const REPORTS_ENABLED = import.meta.env.VITE_ENABLE_REPORTS === 'true'
