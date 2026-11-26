import { readFileSync } from 'node:fs';

const rules = JSON.parse(readFileSync(new URL('../reference/scene-rules.json', import.meta.url), 'utf8'));
export const sessionLimits = rules.message_limits;
export const defaultCastLimit = rules.default_cast_limit;
export const countTolerance = rules.tolerance ?? 0;
