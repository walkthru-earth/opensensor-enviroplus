# Contributing to OpenSensor Enviroplus

## Development Setup

```bash
# Clone the repository
git clone https://github.com/walkthru-earth/opensensor-enviroplus.git
cd opensensor-enviroplus

# Install UV (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync --all-groups

# Setup pre-commit hooks (recommended)
uv run pre-commit install

# Run the CLI
uv run opensensor --help
```

## Code Quality

### Linting and Formatting

This project uses [Ruff](https://docs.astral.sh/ruff/) for both linting and formatting.

```bash
# Check for linting issues
uv run ruff check .

# Auto-fix linting issues
uv run ruff check --fix .

# Check formatting
uv run ruff format --check .

# Auto-format code
uv run ruff format .

# Run both checks (what CI runs)
uv run ruff check . && uv run ruff format --check .
```

### Pre-commit Hooks (Automatic)

After installing pre-commit hooks (`uv run pre-commit install`), Ruff will automatically:
- Check and fix linting issues on every commit
- Format code automatically
- Prevent commits with unfixed issues

```bash
# Install hooks (one-time setup)
uv run pre-commit install

# Run manually on all files (using project dependency)
uv run pre-commit run --all-files

# Run manually using uvx (no installation needed)
uvx pre-commit run --all-files

# Skip hooks if needed (not recommended)
git commit --no-verify
```

### Manual Checks (Without Pre-commit)

```bash
# Lint and format manually
uv run ruff check --fix .
uv run ruff format .

# Verify it works
uv run opensensor --help
uv run opensensor service info
```

## Testing

```bash
# Run basic functionality tests
uv run opensensor --help
uv run opensensor service --help
uv run opensensor service info

# Test service management (requires sudo)
sudo uv run opensensor service install
sudo uv run opensensor service status
sudo uv run opensensor service remove
```

## GitHub Actions

This project uses four automated workflows:

### 1. CI Workflow (`.github/workflows/ci.yml`)
- Runs on push/PR to `main` or `develop`
- Lint & format checking with Ruff
- Tests on Python 3.10, 3.11, 3.12, 3.13
- Package build verification

### 2. Lint Workflow (`.github/workflows/lint.yml`)
- Fast lint-only checks
- Runs on all pushes and PRs
- Uses official Ruff action

### 3. Publish Workflow (`.github/workflows/publish.yml`) ⭐
- **Automatic**: Triggered by version tags (e.g., `v0.2.0`)
- **Manual**: Can be triggered from GitHub UI for TestPyPI or PyPI
- Builds package with UV
- Publishes to PyPI using Trusted Publishers (no API tokens!)
- Creates GitHub releases automatically
- Verifies installation after publish

### 4. Release Workflow (`.github/workflows/release.yml`)
- Legacy workflow triggered by GitHub releases
- Kept for backward compatibility
- Recommend using `publish.yml` for new releases

## Making Changes

1. **Create a branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**
   - Write clean, documented code
   - Follow existing code style
   - Add type hints

3. **Test your changes**
   ```bash
   # Lint and format
   uv run ruff check --fix .
   uv run ruff format .

   # Test functionality
   uv run opensensor --help
   ```

4. **Commit your changes**
   ```bash
   git add .
   git commit -m "feat: add amazing feature"
   ```

5. **Push and create PR**
   ```bash
   git push origin feature/your-feature-name
   ```
   Then create a Pull Request on GitHub

## Commit Message Convention

Use conventional commits:
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `refactor:` - Code refactoring
- `test:` - Adding tests
- `chore:` - Maintenance tasks

## Code Style

- **Type hints**: Use type hints for all functions
- **Docstrings**: Document public functions and classes
- **Imports**: Organized by Ruff (stdlib, third-party, local)
- **Line length**: 100 characters (configured in `pyproject.toml`)
- **Formatting**: Handled automatically by Ruff

## Project Structure

```
opensensor-enviroplus/
├── src/
│   └── opensensor_enviroplus/
│       ├── cli/           # CLI commands
│       ├── collector/     # Data collection
│       ├── config/        # Configuration
│       ├── service/       # Systemd service management
│       ├── sync/          # Cloud sync
│       └── utils/         # Utilities
├── .github/
│   └── workflows/         # CI/CD workflows
├── pyproject.toml         # Project configuration
└── README.md
```

## Publishing Releases

### Prerequisites
1. Configure PyPI Trusted Publisher in your PyPI account settings
2. Add GitHub repository to trusted publishers
3. Set up `pypi` and `testpypi` environments in GitHub repository settings

### Release Process

#### Option 1: Automatic Release (Recommended)
```bash
# 1. Update version in pyproject.toml
# 2. Commit and create a version tag
git add pyproject.toml
git commit -m "chore: bump version to 0.2.0"
git tag v0.2.0
git push origin main --tags

# 3. GitHub Actions automatically:
#    - Builds the package
#    - Publishes to PyPI
#    - Creates GitHub release with notes
#    - Verifies installation
```

#### Option 2: Manual Release (Testing)
```bash
# 1. Go to GitHub Actions → Publish to PyPI 📦
# 2. Click "Run workflow"
# 3. Select target:
#    - testpypi: For testing before production
#    - pypi: For production release
# 4. Click "Run workflow"
```

#### Testing on TestPyPI
```bash
# Install from TestPyPI for testing
uv pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  opensensor-enviroplus

# Test the CLI
opensensor --help
opensensor service info
```

**Note:** Use traditional install for TestPyPI testing. After publishing to production PyPI, users can run `uvx opensensor-enviroplus --help` without installation.

## Questions?

- Open an issue on GitHub
- Check existing issues and discussions
- Read the [ARCHITECTURE.md](ARCHITECTURE.md) for design details

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
