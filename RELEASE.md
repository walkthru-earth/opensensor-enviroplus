# Release Guide

This document explains how to publish new releases of `opensensor-enviroplus` to PyPI.

## 🔐 One-Time Setup: PyPI Trusted Publishers

Trusted Publishers eliminate the need for API tokens. GitHub Actions authenticates directly with PyPI using OIDC.

### 1. Configure PyPI Trusted Publisher

1. **Go to PyPI**: https://pypi.org/manage/account/publishing/
2. **Click**: "Add a new pending publisher"
3. **Fill in**:
   - PyPI Project Name: `opensensor-enviroplus`
   - Owner: `walkthru-earth` (or your GitHub username/org)
   - Repository: `opensensor-enviroplus`
   - Workflow name: `publish.yml`
   - Environment name: `pypi`
4. **Save**

### 2. Configure TestPyPI Trusted Publisher (Optional)

1. **Go to TestPyPI**: https://test.pypi.org/manage/account/publishing/
2. **Click**: "Add a new pending publisher"
3. **Fill in**:
   - PyPI Project Name: `opensensor-enviroplus`
   - Owner: `walkthru-earth`
   - Repository: `opensensor-enviroplus`
   - Workflow name: `publish.yml`
   - Environment name: `testpypi`
4. **Save**

### 3. Create GitHub Environments

1. **Go to**: Repository → Settings → Environments
2. **Create** `pypi` environment:
   - Click "New environment"
   - Name: `pypi`
   - (Optional) Add protection rules: require reviewers for production
3. **Create** `testpypi` environment:
   - Click "New environment"
   - Name: `testpypi`

## 📦 Release Methods

### Method 1: Automatic Release via Git Tags (Recommended)

Best for production releases with full automation.

```bash
# 1. Update version in pyproject.toml
vim pyproject.toml  # Change version = "0.1.0" to "0.2.0"

# 2. Run linting
uv run ruff check --fix .
uv run ruff format .

# 3. Commit changes
git add pyproject.toml
git commit -m "chore: bump version to 0.2.0"

# 4. Create and push tag
git tag v0.2.0
git push origin main
git push origin v0.2.0

# 5. GitHub Actions automatically:
#    ✓ Builds package (wheel + sdist)
#    ✓ Runs linting checks
#    ✓ Publishes to PyPI
#    ✓ Creates GitHub release with auto-generated notes
#    ✓ Verifies installation works
```

**Monitor progress**: https://github.com/walkthru-earth/opensensor-enviroplus/actions

### Method 2: Manual Trigger from GitHub UI

Best for testing on TestPyPI before production release.

**Step 1: Test on TestPyPI**
```bash
# 1. Go to: Actions → Publish to PyPI 📦
# 2. Click "Run workflow"
# 3. Select branch: main
# 4. Select target: testpypi
# 5. Click "Run workflow"

# 6. Wait for completion, then test installation:
uv pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  opensensor-enviroplus

opensensor --help
```

**Step 2: Publish to PyPI**
```bash
# 1. Go to: Actions → Publish to PyPI 📦
# 2. Click "Run workflow"
# 3. Select branch: main
# 4. Select target: pypi
# 5. Click "Run workflow"
```

## 🧪 Testing from TestPyPI

After publishing to TestPyPI, you can test the package using `uvx` (one-shot run without permanent install):

```bash
# Run directly from TestPyPI (recommended - no installation needed)
uvx --index https://test.pypi.org/simple \
    --index https://pypi.org/simple \
    --index-strategy unsafe-best-match \
    opensensor-enviroplus --help

# Or install in a virtual environment for testing
uv pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  opensensor-enviroplus

# Test all commands
opensensor --help
opensensor service --help
opensensor service info
```

**Why multiple `--index` flags?**
- First `--index`: TestPyPI (our package)
- Second `--index`: PyPI (dependencies like polars, typer, etc.)
- `--index-strategy unsafe-best-match`: Allow UV to search all indexes

## 🧪 Pre-Release Checklist

Before creating a release, verify:

- [ ] All CI checks pass: `uv run ruff check . && uv run ruff format --check .`
- [ ] Version updated in `pyproject.toml`
- [ ] CHANGELOG updated (if you maintain one)
- [ ] All tests pass: `uv run opensensor --help && uv run opensensor service --help`
- [ ] Documentation is up to date
- [ ] Local build works: `uv build`
- [ ] TestPyPI installation works: `uvx --index https://test.pypi.org/simple --index https://pypi.org/simple --index-strategy unsafe-best-match opensensor-enviroplus --help`

## 📋 Version Numbering

Follow [Semantic Versioning](https://semver.org/):

- **MAJOR** (v1.0.0 → v2.0.0): Breaking changes
- **MINOR** (v0.1.0 → v0.2.0): New features, backward compatible
- **PATCH** (v0.1.0 → v0.1.1): Bug fixes, backward compatible

**Examples:**
- `v0.1.0` - Initial release
- `v0.2.0` - Added new sensor support
- `v0.2.1` - Fixed sync bug
- `v1.0.0` - Stable API, production ready

## 🔍 Verification After Release

### Check PyPI
```bash
# View package page
open https://pypi.org/project/opensensor-enviroplus/

# Install from PyPI
uv pip install opensensor-enviroplus

# Test CLI commands
opensensor --help
opensensor service info
```

### Check GitHub Release
```bash
# View release page
open https://github.com/walkthru-earth/opensensor-enviroplus/releases

# Verify:
# ✓ Release notes auto-generated
# ✓ Source code archives attached
# ✓ Wheel and sdist attached
```

## 🐛 Troubleshooting

### "Publishing forbidden" error
**Problem**: PyPI rejects the upload with permission error.

**Solution**:
1. Verify Trusted Publisher is configured correctly on PyPI
2. Check environment name matches exactly: `pypi` (not `PyPI` or `production`)
3. Ensure `id-token: write` permission is set in workflow

### "Package already exists" error
**Problem**: Version already published to PyPI.

**Solution**:
1. PyPI doesn't allow overwriting versions
2. Bump version in `pyproject.toml`
3. Create new tag: `git tag v0.2.1 && git push --tags`

### Verification fails
**Problem**: `uv pip install opensensor-enviroplus` fails after publish.

**Solution**:
1. Wait 60 seconds for PyPI CDN to update
2. Check package exists: https://pypi.org/project/opensensor-enviroplus/
3. Try with `--no-cache`: `uv pip install --no-cache opensensor-enviroplus`

### Build fails in CI
**Problem**: `uv build` fails in GitHub Actions.

**Solution**:
1. Test build locally: `uv build`
2. Check `pyproject.toml` syntax
3. Verify all dependencies are available
4. Check CI logs for specific error

## 📚 References

- [PyPI Trusted Publishers](https://docs.pypi.org/trusted-publishers/)
- [UV Build Documentation](https://docs.astral.sh/uv/guides/publish/)
- [GitHub Actions Publish](https://packaging.python.org/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)
- [Semantic Versioning](https://semver.org/)

## 🎯 Quick Commands

```bash
# Check current version
grep 'version =' pyproject.toml

# Build locally
uv build

# List built packages
ls -lh dist/

# Clean build artifacts
rm -rf dist/ build/ *.egg-info

# Test installation from local build
uv pip install dist/opensensor_enviroplus-0.2.0-py3-none-any.whl

# View PyPI stats (requires pypistats)
# uv pip install pypistats
# pypistats recent opensensor-enviroplus
```

## 📝 Release Workflow Summary

```mermaid
graph LR
    A[Update Version] --> B[Commit Changes]
    B --> C[Create Tag]
    C --> D[Push to GitHub]
    D --> E[GitHub Actions Triggered]
    E --> F[Build Package]
    F --> G[Lint & Test]
    G --> H[Publish to PyPI]
    H --> I[Create GitHub Release]
    I --> J[Verify Installation]
```

**Happy Releasing! 🚀**
