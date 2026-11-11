# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial scaffold: mandate model, bounds enforcement, evidence chain

### Added (v0.2.0)
- Concurrency safety — `enforce()` holds the mandate lock across check+record (#12)
- `StateStore` protocol + `InMemoryStore` + `SQLiteStore` — persistent velocity/spend state (#7)
- `Bounds.total_budget` + `budget_window_days` — cumulative spend caps (#8)
