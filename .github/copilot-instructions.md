# GitHub Copilot Instructions for Availability Monitor

## Project Overview
Availability Monitor is a Python/Electron launcher application that monitors product availability across multiple stores (Amazon, Target, Walmart) and sends Telegram alerts. The project uses CI/CD with GitHub Actions for automated builds and releases.

## Branch Workflow - MANDATORY

### Feature Development
When working on **any new feature, enhancement, or bug fix**:

1. **Create a dedicated feature branch** (DO NOT work on main):
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b bugfix/issue-description
   git checkout -b enhancement/feature-name
   ```

2. **Naming convention**:
   - `feature/` prefix for new features
   - `bugfix/` prefix for bug fixes
   - `enhancement/` prefix for improvements
   - `docs/` prefix for documentation
   - Examples: `feature/electron-launcher`, `bugfix/workflow-powershell`, `enhancement/ui-improvements`

3. **Commit message format**:
   ```
   [type]: brief description
   
   - Detailed change 1
   - Detailed change 2
   
   Examples:
   feat: add new monitoring feature
   fix: resolve PowerShell parsing error
   docs: update README installation steps
   ```

4. **Before merging back to main**, ensure the feature branch has:
   - ✅ All changes committed
   - ✅ Branch pushed to GitHub
   - ✅ No merge conflicts with main

### Release & Tag Workflow - CRITICAL

**BEFORE considering a feature "done", you MUST:**

1. **Verify versions are synchronized**:
   ```
   pyproject.toml: version = "X.Y.Z"
   electron/package.json: "version": "X.Y.Z"
   ```
   → Both MUST match exactly (e.g., both "0.1.2")

2. **Commit version bump if not already done**:
   ```bash
   git add pyproject.toml electron/package.json
   git commit -m "bump: version X.Y.Z - [brief description]"
   ```

3. **Create and push the tag** (this triggers GitHub Actions):
   ```bash
   git tag vX.Y.Z
   git push origin feature/branch-name --tags
   ```

4. **Monitor GitHub Actions**:
   - Go to https://github.com/RyuKun24/availability-change-alert-sender/actions
   - Wait for the "Build and Release" workflow to complete
   - Verify it shows ✅ SUCCESS (not ❌ FAILED)

5. **Verify release was created**:
   - Visit https://github.com/RyuKun24/availability-change-alert-sender/releases
   - Confirm release vX.Y.Z appears with downloadable .exe files:
     - `availability_monitor.exe` (Python backend)
     - `Availability Monitor Setup *.exe` (NSIS installer)
     - `Availability Monitor-*.exe` (Portable executable)

6. **If build FAILED**:
   - ❌ DO NOT proceed
   - Identify the error in GitHub Actions logs
   - Fix the issue locally
   - Bump version to next patch: `vX.Y.(Z+1)`
   - Repeat steps 3-5 until ✅ SUCCESS

7. **Manual merge to main (after successful release)** ⚠️ **REQUIRES OWNER REVIEW**:
   - ✅ Build succeeded and release artifacts verified
   - ⏸️ STOP - Wait for owner review of changes and .exe files
   - When approved by owner:
   ```bash
   git checkout main
   git merge feature/your-feature-name
   git push origin main
   ```
   - Then delete the remote feature branch:
   ```bash
   git push origin --delete feature/your-feature-name
   ```
   - Optional: Tag main with production version (e.g., v1.0.0)
   ```bash
   git tag v1.0.0 main
   git push origin v1.0.0
   ```

## Version Numbering (Semantic Versioning)

- **MAJOR.MINOR.PATCH** (e.g., 0.1.2)
- `MAJOR`: Breaking changes
- `MINOR`: New features
- `PATCH`: Bug fixes
- Current development: `0.1.x` (pre-release phase)
- Production ready: `1.0.0+`

## GitHub Actions Workflow (Automatic)

When a tag `v*` is pushed:
1. ✅ Checkout code
2. ✅ Verify versions sync (pyproject.toml = package.json = tag version)
3. ✅ Install Python & Node dependencies
4. ✅ Build Python executable (PyInstaller)
5. ✅ Build Electron app (electron-builder NSIS + portable)
6. ✅ Create GitHub Release with all .exe files
7. 📊 Auto-updater (electron-updater) will detect new releases

## Common Issues & Fixes

### Build Failed: "Version mismatch"
**Problem**: pyproject.toml version ≠ package.json version
**Solution**: Update both to same version, commit, tag again

### Build Failed: "PowerShell parse error"
**Problem**: Unicode characters or quote escaping issues
**Solution**: Use proper PowerShell syntax (Write-Host instead of echo)

### Build Failed: "npm ci" or "pip install" errors
**Problem**: Dependency installation failed
**Solution**: Check network, retry tag, update requirements.txt if needed

### Tag already exists
**Problem**: Tag vX.Y.Z already created
**Solution**: 
```bash
git tag -d vX.Y.Z              # delete local tag
git push origin :refs/tags/vX.Y.Z  # delete remote tag
# Then create new version (vX.Y.(Z+1))
```

## File Locations

- **Python versions**: `pyproject.toml` (line ~3)
- **Electron versions**: `electron/package.json` (line ~2)
- **Workflow config**: `.github/workflows/build-release.yml`
- **Python code**: `src/availability_alert/`
- **Electron code**: `electron/`
- **Launcher wrapper**: `launcher/`

## Distribution Files

When release is complete, **distribution for end users**:

| File | Purpose | For |
|------|---------|-----|
| `Availability.Monitor.Setup.X.Y.Z.exe` | **← GIVE THIS ONE** - Windows installer with auto-shortcut | End users / Your wife |
| `Availability.Monitor-X.Y.Z.exe` | Portable (no installation needed) | Alternative if needed |
| `availability_monitor.exe` | Python backend only | Developers/advanced users |

**Recommendation**: Use the **Setup installer** - it's user-friendly and standard.

## Development Best Practices

✅ **DO**:
- Create feature branches for all work
- Test locally before pushing tags
- Verify GitHub Actions succeeds before declaring "done"
- Keep commits atomic and descriptive
- Update version numbers before releases
- Document changes in commit messages

❌ **DON'T**:
- Commit directly to main
- Create tags without first testing locally
- Ignore GitHub Actions failures
- Create tags with mismatched versions
- Force push to main (unless absolutely necessary)
- Forget to verify release files on GitHub

## Quick Reference Commands

```bash
# Start a new feature
git checkout -b feature/my-feature

# Prepare for release
git add pyproject.toml electron/package.json
git commit -m "bump: version 0.1.3 - description"

# Create and push tag
git tag v0.1.3
git push origin feature/my-feature --tags

# Monitor build
# Open: https://github.com/RyuKun24/availability-change-alert-sender/actions

# After successful build, WAIT FOR REVIEW then manually merge to main
git checkout main
git merge feature/my-feature
git push origin main

# Delete remote branch after merge
git push origin --delete feature/my-feature

# Optional: tag main with production version
git tag v1.0.0 main
git push origin v1.0.0
```

## Manual Review Process for Production Releases

**Starting with v0.1.13 and forward, all merges to main require manual owner review:**

1. **Feature branch builds successfully** → Release created on GitHub
2. **Owner reviews**:
   - Check changes in the feature branch
   - Download and test the .exe files
   - Verify release artifacts are correct
3. **Owner approves** → Manually execute merge to main:
   ```bash
   git checkout main
   git merge feature/your-feature-name
   git push origin main
   git push origin --delete feature/your-feature-name
   ```
4. **Optional**: Create production version tag
   ```bash
   git tag v1.0.0 main  # Only when ready for major release
   git push origin v1.0.0
   ```

**This ensures production code is always reviewed before going live.**

## Questions?

Refer to this file when:
- Starting a new feature
- Before creating tags/releases
- Debugging GitHub Actions failures
- Uncertain about version numbering

Ask Copilot: "What's the release workflow?" or "How should I create a feature branch?"

