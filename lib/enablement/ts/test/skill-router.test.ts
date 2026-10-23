import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tokenize, scoreCard, selectCards, packContext, type SkillCard, type Chunk } from '../src/skill-router.js';

const card = (name: string, description: string, triggers: string[], costUnits = 1, kind: 'skill' | 'mcp_tool' = 'skill'): SkillCard =>
  ({ name, description, triggers, kind, costUnits });

test('tokenize: lowercase, unicode, drops 1-char tokens', () => {
  assert.deepEqual(tokenize('Sök i Avtal-PDF:er, a b SQL2 sql2!'), ['sök', 'avtal', 'pdf', 'er', 'sql2', 'sql2']);
  assert.deepEqual(tokenize('  '), []);
});

test('scoreCard: triggers weigh 2, name/description 1, distinct query tokens', () => {
  const c = card('contract-search', 'Semantic search over contracts', ['avtal', 'contract']);
  assert.equal(scoreCard('contract contract search avtal', c), 2 + 1 + 2);
  assert.equal(scoreCard('weather', c), 0);
});

test('selectCards: order by score, cost, name; budget skip; k limit; no mutation', () => {
  const cards = [card('zeta', 'invoice search', ['invoice'], 1), card('alpha', 'invoice search', ['invoice'], 1),
    card('pricey', 'invoice search tool', ['invoice', 'search'], 9), card('cheap', 'search', [], 1, 'mcp_tool'), card('none', 'weather', [], 1)];
  const before = cards.map((c) => c.name);
  assert.deepEqual(selectCards('invoice search', cards, 3, 100).map((c) => c.name), ['pricey', 'alpha', 'zeta']);
  assert.deepEqual(selectCards('invoice search', cards, 3, 3).map((c) => c.name), ['alpha', 'zeta', 'cheap']);
  assert.deepEqual(selectCards('invoice search', cards, 1, 3).map((c) => c.name), ['alpha']);
  assert.deepEqual(cards.map((c) => c.name), before);
  assert.deepEqual(selectCards('weather', [], 3, 10), []);
});

test('packContext: score order, id tiebreak, budget skip, distinct sources', () => {
  const chunks: Chunk[] = [
    { id: 'c3', text: 'refund policy for orders', source: 'faq.md' },
    { id: 'c1', text: 'refund within 30 days', source: 'policy.pdf' },
    { id: 'c2', text: 'refund policy orders and refund details in a very long paragraph that will not fit', source: 'policy.pdf' },
    { id: 'c4', text: 'shipping times', source: 'faq.md' },
  ];
  const p = packContext('refund policy orders', chunks, 50);
  assert.deepEqual(p.ids, ['c3', 'c1']);
  assert.equal(p.usedChars, 'refund policy for orders'.length + 'refund within 30 days'.length);
  assert.deepEqual(p.sources, ['faq.md', 'policy.pdf']);
  const all = packContext('refund policy orders', chunks, 1000);
  assert.deepEqual(all.ids, ['c2', 'c3', 'c1']);
  assert.deepEqual(all.sources, ['policy.pdf', 'faq.md']);
  assert.deepEqual(packContext('nothing', chunks, 1000), { ids: [], usedChars: 0, sources: [] });
});
