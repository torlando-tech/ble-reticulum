# Contributing to Reticulum BLE Interface

Thank you for your interest in contributing! This document provides guidelines and information for contributors.

**Note:** This guide is for **developing/contributing** to the BLE interface code itself. If you want to **use** the BLE interface in your Reticulum setup, see the [Installation section in README.md](README.md#installation).

## Getting Started

### Prerequisites

- Python 3.8 or higher
- Git
- Linux system with BlueZ 5.x
- BLE-enabled hardware for integration testing (Raspberry Pi Zero W recommended, but not required for unit tests)

### Development Setup

**Important:** Development uses a virtual environment isolated from your Reticulum installation. This prevents conflicts and allows testing without affecting your production setup.

1. **Fork and clone the repository**
   ```bash
   git clone https://github.com/YOUR-USERNAME/ble-reticulum.git
   cd ble-reticulum
   ```

2. **Create and activate virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Linux/macOS
   ```

3. **Install Reticulum** (required for tests)
   ```bash
   pip install rns
   ```

4. **Install dependencies** (includes runtime and development dependencies)
   ```bash
   pip install -r requirements-dev.txt
   ```

5. **Create package structure** (required for imports in tests)
   ```bash
   touch src/RNS/__init__.py
   touch src/RNS/Interfaces/__init__.py
   ```

6. **Run tests to verify setup**
   ```bash
   pytest
   ```

All tests should pass. If you encounter errors, check that you're in the virtual environment and all dependencies are installed.

## Development Workflow

### 1. Create a Branch

Create a feature branch for your work:

```bash
git checkout -b feature/your-feature-name
```

Use descriptive branch names:
- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation updates
- `test/` - Test improvements

### 2. Make Changes

- Follow existing code style and conventions
- Add tests for new functionality
- Update documentation as needed
- Keep commits focused and atomic

### 3. Run Tests

Before submitting, ensure all tests pass:

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src/RNS/Interfaces

# Run specific test file
pytest tests/test_fragmentation.py -v
```

### 4. Commit Changes

Use clear, descriptive commit messages:

```bash
git commit -m "feat: Add connection retry backoff"
git commit -m "fix: Handle GATT disconnection edge case"
git commit -m "docs: Update configuration examples"
```

### 5. Submit Pull Request

1. Push your branch to your fork
2. Open a pull request against the main repository
3. Describe your changes clearly
4. Reference any related issues

## Code Style

### Python Style

- Follow PEP 8 guidelines
- Maximum line length: 100 characters
- Use meaningful variable names

### Code Organization

- Keep functions focused and single-purpose
- Add docstrings to all public functions and classes
- Use type hints where appropriate
- Handle errors gracefully with proper exception handling

### Example

```python
def fragment_packet(self, packet: bytes, mtu: int = 185) -> List[bytes]:
    """
    Fragment a packet into BLE-sized chunks.

    Args:
        packet: The packet data to fragment
        mtu: Maximum transmission unit size (default: 185)

    Returns:
        List of packet fragments with headers

    Raises:
        ValueError: If packet is empty or MTU is too small
    """
    if not packet:
        raise ValueError("Cannot fragment empty packet")
    # ... implementation
```

## Testing Guidelines

### Writing Tests

- Write tests for all new functionality
- Use descriptive test names: `test_fragment_packet_handles_empty_input`
- Test both success and failure cases
- Use pytest fixtures for common setup

### Test Organization

- Unit tests: Test individual components in isolation
- Integration tests: Test component interactions
- Use mocks for external dependencies (BLE hardware)

### Example Test

```python
def test_fragmenter_handles_large_packet():
    """Test that fragmenter correctly splits packets larger than MTU"""
    fragmenter = BLEFragmenter(mtu=185)
    large_packet = b"x" * 500

    fragments = fragmenter.fragment_packet(large_packet)

    assert len(fragments) > 1
    assert all(len(f) <= 185 for f in fragments)
```

## Documentation

### Code Documentation

- Add docstrings to all public functions and classes
- Include parameter descriptions and return values
- Document exceptions that may be raised
- Provide usage examples in docstrings

### User Documentation

- Update README.md for user-facing changes
- Update examples/ for configuration changes
- Add troubleshooting tips for common issues
- Keep documentation clear and concise

## Bug Reports

When reporting bugs, please include:

1. **Description**: Clear description of the issue
2. **Steps to reproduce**: Exact steps to trigger the bug
3. **Expected behavior**: What should happen
4. **Actual behavior**: What actually happens
5. **Environment**:
   - OS and version
   - Python version
   - Reticulum version
   - BLE hardware
6. **Logs**: Relevant log output (use `rnsd --verbose`)

### Example Bug Report

```
**Bug**: GATT server fails to start on Raspberry Pi Zero W

**Steps to reproduce**:
1. Install on fresh Raspberry Pi Zero W
2. Configure BLE interface in ~/.reticulum/config
3. Run `rnsd --verbose`

**Expected**: GATT server starts and advertises

**Actual**: Error "Failed to register GATT application"

**Environment**:
- OS: Raspberry Pi OS (Debian 11)
- Python: 3.9.2
- Reticulum: 1.0.0
- Hardware: Raspberry Pi Zero W (built-in BLE)

**Logs**:
[2025-10-26 10:15:23] [ERROR] GATT server registration failed
...
```

## Feature Requests

When suggesting features:

1. **Use case**: Describe the problem you're trying to solve
2. **Proposed solution**: How you think it should work
3. **Alternatives**: Other solutions you've considered
4. **Impact**: Who would benefit from this feature

## Review Process

### Pull Request Review

Pull requests will be reviewed for:

- **Functionality**: Does it work as intended?
- **Tests**: Are there adequate tests?
- **Code quality**: Is the code clean and maintainable?
- **Documentation**: Is it properly documented?
- **Compatibility**: Does it maintain backward compatibility?

### Review Timeline

- Small fixes: Usually reviewed within 1-3 days
- New features: May take 5-7 days for thorough review
- Complex changes: May require multiple review rounds

## Questions?

If you have questions about contributing:

- Open an issue with the `question` label
- Check existing issues and pull requests
- Review the documentation in the repository

## Code of Conduct

- Be respectful and constructive
- Welcome newcomers and help them learn
- Focus on the code, not the person
- Give and receive feedback gracefully

Thank you for contributing to Reticulum BLE Interface!
