# JEV Data Contract

## Purpose

Define the data boundary between existing AI-Trading decision systems and JEV.

JEV consumes frozen decision context and produces explanation records. JEV does not replace upstream decision generation.

## Input Source

Primary source:

`OpportunityDecisionSnapshot`

Flow:

Events → Picks → OpportunityDecisionSnapshot → JEV

## JEV Input

```json
{
  "snapshot_id": "",
  "symbol": "",
  "name": "",
  "stage": "",
  "scenario": "",
  "evidence": {},
  "decision": {}
}
```

## JEV Output

```json
{
  "snapshot_id": "",
  "summary": "",
  "formation_chain": [],
  "supporting_factors": [],
  "uncertainty": [],
  "conflicts": [],
  "confidence": {}
}
```

## Principles

1. Decision facts and JEV explanations are separated.
2. Historical decision snapshots must not be rewritten by later results.
3. JEV output must be versioned.
4. Missing JEV availability must not block core trading research flow.
5. Evidence must be traceable.

## Future Extensions

- JEV Timeline
- Evidence Confidence
- JEV version comparison
