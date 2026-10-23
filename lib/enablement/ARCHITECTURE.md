# Reference architecture: retrieval, skills and agents with a verification layer

How the pieces in this repo fit together when an organisation wants AI search and agent-driven flows over its own
unstructured data (documents, tickets, wikis). Deterministic code does the routing, counting and checking; the model
does the language work in between, and its output is verified before anyone acts on it.

```
 sources (PDF, wiki, tickets)      skill library (SKILL.md)      MCP connectors (tools)
          │ ingest + chunk                 │ skill-lint                    │ intake screening
          ▼                                ▼                               ▼
   chunk store ──► packContext() ──► agent step ◄── selectCards() ◄── skill/tool cards
                  (budgeted context)     │ (model)       (which skills/tools to load, within a cost budget)
                                         ▼
                           verification layer: schema-guard, grounding (citations ⊆ retrieved),
                           secrets-scan, trace evals (agent-trace-evals), regression gate in CI
                                         ▼
                           human approval for anything that writes, spends or sends
                                         ▼
                           adoption + cost KPIs per function (enablement-kit --action adoption)
```

## Building blocks
| layer | here | notes |
|---|---|---|
| skill / tool selection | `ts/src/skill-router.ts` (`selectCards`) | strict TypeScript, no dependencies; triggers weigh more than descriptions; hard cost budget |
| context packing | `ts/src/skill-router.ts` (`packContext`) | lexical baseline; swap in embeddings or hybrid search behind the same interface |
| skill library hygiene | `enablement-kit.py --action skill-lint` | portable Agent Skills: name/folder, when-to-use description, no personal paths or credentials |
| connector governance | `enablement-kit.py --action intake` | fast-track / forum review / reject for new AI tools and MCP connectors |
| output verification | `lib/schema-guard.py`, `lib/secrets-scan.py`, `PLAYBOOK.md` (three levels) | schemas and checks before a human sees the output |
| measurement | `enablement-kit.py --action adoption` | weekly active users, maturity 0–4 per function, cost per active user |

## Design choices
- **Retrieval behind an interface.** `packContext` is a lexical baseline so the evaluation (recall@k, groundedness) can be set up
  before choosing a vector store; the store is a replaceable component, not the architecture.
- **Load few skills, not all.** Selecting skills and MCP tools per request keeps the context small and the tool surface auditable.
- **Every write is gated.** Agents propose; actions that change data, spend money or send messages need a human approval step.
- **Measure adoption and cost from day one**, so scaling decisions rest on usage, not on demos.

## Run the TypeScript part
```bash
cd lib/enablement/ts && npm install && npm test   # tsc --strict, then node --test (4 tests)
```
