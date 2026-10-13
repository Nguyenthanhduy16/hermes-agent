// Standard Node.js Vercel Function. Shared code lives outside api/ so it is
// not exposed as another function. NODEJS_HELPERS=0 preserves the raw JSON.
import { createHandler } from '../lib/gateway.js';

export default createHandler();
