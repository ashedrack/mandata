# Contributing to Mandata

Thanks for your interest in contributing. Mandata is young — the best way in is a
`good first issue`, or the roadmap item that matches your skills.

## Ways to contribute

- **Code** — pick an issue, open a PR
- **Spec & docs** — the mandate model is still being formalized; review, critique, extend
- **Protocol adapters** — AP2 / x402 / card-network integrations
- **Language ports** — TypeScript implementation is on the roadmap
- **Threat models** — attack scenarios the enforcement layer must survive

## Setup

```bash
git clone https://github.com/ashedrack/mandata.git
cd mandata
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Conventions

- **Conventional Commits** — `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`
- **Branching** — fork → feature branch → PR against `main`
- **Style** — ruff-enforced (line length 100); `ruff check . && ruff format .` before pushing
- **Tests** — pytest; new behavior needs new tests
- **One thing per PR** — small, reviewable changes merge faster

## PR process

1. Open an issue first for anything beyond a small fix — align before building
2. Fill in the PR template (what + why + how tested)
3. CI must be green; a maintainer reviews within a few days
4. Squash-merge on approval

## Code of Conduct

Participation means agreeing to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Questions

Open a discussion or an issue — no question is too basic.
