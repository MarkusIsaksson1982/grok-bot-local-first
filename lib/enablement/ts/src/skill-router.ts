// skill-router.ts – pick which Agent Skills / MCP tools to load for a request, and pack retrieved chunks into a context budget.
// Dependency-free, strict TypeScript. Deterministic on purpose: the model step comes after this, never instead of it.
export interface SkillCard { name: string; description: string; triggers: string[]; kind: 'skill' | 'mcp_tool'; costUnits: number; }
export interface Chunk { id: string; text: string; source: string; }
export interface Packed { ids: string[]; usedChars: number; sources: string[]; }

export function tokenize(text: string): string[] {
  return text.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter((t) => t.length >= 2);
}

export function scoreCard(query: string, card: SkillCard): number {
  const trig = new Set(tokenize(card.triggers.join(' ')));
  const body = new Set(tokenize(card.name + ' ' + card.description));
  let s = 0;
  for (const t of new Set(tokenize(query))) s += trig.has(t) ? 2 : body.has(t) ? 1 : 0;
  return s;
}

export function selectCards(query: string, cards: SkillCard[], k: number, maxCostUnits: number): SkillCard[] {
  const ranked = cards.map((c) => ({ c, s: scoreCard(query, c) })).filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || a.c.costUnits - b.c.costUnits || a.c.name.localeCompare(b.c.name));
  const out: SkillCard[] = []; let cost = 0;
  for (const { c } of ranked) {
    if (out.length >= k) break;
    if (cost + c.costUnits > maxCostUnits) continue;
    out.push(c); cost += c.costUnits;
  }
  return out;
}

export function packContext(query: string, chunks: Chunk[], budgetChars: number): Packed {
  const q = new Set(tokenize(query));
  const ranked = chunks.map((c) => { const ts = new Set(tokenize(c.text)); let s = 0; for (const t of q) if (ts.has(t)) s++; return { c, s }; })
    .filter((x) => x.s > 0).sort((a, b) => b.s - a.s || a.c.id.localeCompare(b.c.id));
  const ids: string[] = []; const sources: string[] = []; let used = 0;
  for (const { c } of ranked) {
    if (used + c.text.length > budgetChars) continue;
    ids.push(c.id); used += c.text.length;
    if (!sources.includes(c.source)) sources.push(c.source);
  }
  return { ids, usedChars: used, sources };
}
