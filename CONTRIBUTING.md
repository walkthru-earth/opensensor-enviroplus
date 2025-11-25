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

# Run manually on all files
uv run pre-commit run --all-files

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

This project uses three automated workflows:

### 1. CI Workflow (`.github/workflows/ci.yml`)
- Runs on push/PR to `main` or `develop`
- Lint & format checking with Ruff
- Tests on Python 3.10, 3.11, 3.12
- Package build verification

### 2. Lint Workflow (`.github/workflows/lint.yml`)
- Fast lint-only checks
- Runs on all pushes and PRs
- Uses official Ruff action

### 3. Release Workflow (`.github/workflows/release.yml`)
- Triggered when a GitHub release is published
- Builds and publishes to PyPI
- Uses trusted publishing (no API tokens needed)

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

## Questions?

- Open an issue on GitHub
- Check existing issues and discussions
- Read the [ARCHITECTURE.md](ARCHITECTURE.md) for design details

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
