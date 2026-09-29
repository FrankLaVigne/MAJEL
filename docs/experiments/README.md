# Experiments

MAJEL's architecture is meant to be discovered, not assumed. Experiments are how the project produces the evidence behind its [architecture decisions](../decisions/README.md).

An experiment is worth running when it can confirm, refute, or refine one of MAJEL's [architectural principles](../../README.md#architectural-principles), or when it answers a question a design decision depends on.

## What every experiment documents

Each experiment should record:

1. **Hypothesis:** what you expect to happen, and why. It should be specific enough to be wrong.
2. **Architecture / configuration:** the agents, providers, interfaces, trust boundaries, and versions involved. Include enough detail to reproduce it, and **never** include credentials or private data.
3. **Procedure:** the steps taken, in order.
4. **Measurements:** what was measured and how, such as latency, failure recovery time, leaked information, or policy violations.
5. **Observations:** what actually happened, including the unexpected.
6. **Failures:** what broke, what didn't work, and what was abandoned. Negative results are results.
7. **Conclusions:** what the evidence supports, and how confident you are.
8. **Resulting architectural changes:** changes to principles, ADRs, or structure that follow from the conclusions. Write "none" if nothing changed.

## Layout

Each experiment gets its own file or directory, numbered sequentially:

```text
docs/experiments/
├── README.md
├── 0001-short-title.md
└── 0002-short-title/
    ├── README.md
    └── supporting files (diagrams, sanitized logs, measurements)
```

Sanitize everything before committing. Logs and transcripts from agent systems often contain personal data, tokens, or hostnames.

## Candidate experiments

These are proposed experiments. **None have been run yet.**

- Delegating a task between independently trusted agents
- Surviving the loss of an AI compute provider
- Moving an agent workload without losing persistent identity
- Measuring cross-domain information leakage
- Testing prompt-injection propagation across agent boundaries
- Comparing a federated architecture with a single omnipotent agent
- Routing a capability to different compute providers

## Index

*No experiments have been recorded yet.*
