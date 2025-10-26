# CI/CD Workflows

This directory contains GitHub Actions and Gitea Actions workflows for automated testing.

## Workflows

### test.yml - Automated Test Suite

This workflow runs on every push and pull request. It includes **two separate jobs** that run in parallel:

#### Job 1: Unit Tests
- **Purpose**: Test core fragmentation and prioritization logic
- **Files tested**:
  - `tests/test_fragmentation.py` 
  - `tests/test_prioritization.py` 
- **Coverage**: `BLEFragmentation.py` module
- **Matrix**: Python 3.8, 3.9, 3.10, 3.11

#### Job 2: Integration Tests
- **Purpose**: Test full BLE stack integration without hardware
- **Files tested**: All test files with marker `-m "not hardware"`
- **Coverage**: All `src/RNS/Interfaces/` modules
- **Runtime**: ~2 minutes per Python version
- **Matrix**: Python 3.8, 3.9, 3.10, 3.11
- **Tests included**:
  - Error recovery tests
  - Peer interface tests
  - Integration tests
  - Prioritization tests
  - Plus fragmentation unit tests 

## PR Status Checks

When you create a pull request, you'll see two separate status checks:

```
✓ Unit Tests (Python 3.8)
✓ Unit Tests (Python 3.9)
✓ Unit Tests (Python 3.10)
✓ Unit Tests (Python 3.11)

✓ Integration Tests (Python 3.8)
✓ Integration Tests (Python 3.9)
✓ Integration Tests (Python 3.10)
✓ Integration Tests (Python 3.11)
```

Both sets of checks must pass before merging.

## Coverage Reports

Coverage reports are uploaded to Codecov for Python 3.11 runs:

- **Unit coverage**: Tagged with `flags: unit`
- **Integration coverage**: Tagged with `flags: integration`

This allows tracking coverage trends separately for unit vs integration tests.

## Local Testing

To run the same tests locally that CI runs:

```bash
# Unit tests
pytest tests/test_fragmentation.py tests/test_prioritization.py -v \
  --cov=src/RNS/Interfaces/BLEFragmentation.py \
  --cov-report=term-missing

# Integration tests
pytest tests/ -v -m "not hardware" \
  --cov=src/RNS/Interfaces \
  --cov-report=term-missing \
  --tb=short
```

## Why Two Jobs?

Separating unit and integration tests provides several benefits:

1. **Faster Feedback**: Unit tests complete quickly (~30s), giving rapid feedback
2. **Clearer Failures**: Know immediately if it's a core logic issue or integration problem
3. **Parallel Execution**: Both jobs run simultaneously, total time = max(unit, integration)
4. **Separate Coverage**: Track unit test coverage separately from integration coverage
5. **Granular Status**: See exactly which test category failed in PR checks

## Workflow Triggers

Both workflows trigger on:
- **Push** to any branch
- **Pull request** to any branch

## Dependencies

The workflows install:
- System: `libglib2.0-dev`, `libdbus-1-dev` (for BLE D-Bus support)
- Python: `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-timeout`
- BLE: `bleak` (BLE client library), `bluezero` (GATT server), `dbus-python`
- Reticulum: `rns` (required for tests)

## Modifying Workflows

To add new tests:

1. Add test file to `tests/` directory
2. Mark appropriately:
   - Unit tests: Include in unit test job command
   - Integration tests: Will run automatically with `-m "not hardware"`
   - Hardware tests: Mark with `@pytest.mark.hardware` to exclude from CI

The workflow will automatically pick up marked integration tests.

## Troubleshooting

### Workflow not triggering
- Check that workflow file is in `.github/workflows/` (GitHub) or `.gitea/workflows/` (Gitea)
- Ensure YAML syntax is valid
- Check branch name matches trigger pattern

### Tests failing in CI but passing locally
- Check Python version (CI tests multiple versions)
- Verify all dependencies are in `requirements.txt`
- Check for environment-specific paths or configs

### Coverage upload failing
- This is non-fatal (continue-on-error: true)
- Usually due to Codecov token issues
- Tests still pass/fail correctly

## Related Documentation

- Testing guide: [TESTING.md](../../TESTING.md)
- Contributing guide: [CONTRIBUTING.md](../../CONTRIBUTING.md)
- Project README: [README.md](../../README.md)
