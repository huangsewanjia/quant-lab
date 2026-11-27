# Changelog

## [Unreleased]

### Fixed
- **Keep WORK-557 training headless on Windows** (2026-11-27): Set the Matplotlib backend before training imports; the regression check reports `backend=Agg` and `figure_ok=True`.
