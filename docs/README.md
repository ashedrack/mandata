# Documentation guide

Where to start depends on what you are trying to do.

## I want to use the library

1. [README](../README.md) gives the install command and a working quickstart in about forty lines.
2. [docs/api-reference.md](api-reference.md) lists the complete public API — every function and class, grouped by job, with a minimal example of each.
3. [examples/basic_usage.py](../examples/basic_usage.py) shows the smallest complete flow: issue a mandate, enforce a transaction, read the decision.
4. [examples/end_to_end.py](../examples/end_to_end.py) walks a full authorize, claim, settle cycle in one process.
5. [examples/demo_app.py](../examples/demo_app.py) runs a simulated deployment over real HTTP: registry service, mock payment rail, delegation, revocation, and a full evidence audit at the end. Standard library only.

## I want to understand the model

1. [docs/mandate-model.md](mandate-model.md) explains the schema: what a mandate is, what bounds it carries, and how a principal's intent becomes an enforceable constraint.
2. [docs/architecture.md](architecture.md) maps the modules to the mandate chain: models, enforce, evidence, delegation, credentials.
3. [docs/diagrams.md](diagrams.md) has the system design diagrams.
4. [docs/threat-model.md](threat-model.md) lists the attacks the gate is built to survive and which defenses are currently in place.

## I want to build a conforming implementation

1. Read [spec/mmf-v0.3.24.md](../spec/mmf-v0.3.24.md), the current normative specification. Older spec versions are kept alongside it for reference.
2. [docs/conformance.md](conformance.md) explains how the vector corpus works, what the expected fields mean, and how to classify your results.
3. [spec/FAQ.md](../spec/FAQ.md) records settled rulings from independent conformance runs. If your implementation disagrees with a vector, check here first, the question has likely come up before.
4. Open an issue on the repo if a vector looks wrong or ambiguous. Conformance questions get spec-level answers, not just code comments.

## I want to contribute

[CONTRIBUTING.md](../CONTRIBUTING.md) covers setup and workflow, and issues labelled `good first issue` are the intended entry points. [GOVERNANCE.md](../GOVERNANCE.md) explains how decisions get made.

## I want the history

[CHANGELOG.md](../CHANGELOG.md) documents every release and the reasoning behind each normative change, including changes that were later withdrawn or narrowed. It is the most honest record of how the spec got here.
