"""
Build script for PyInstaller compilation to standalone .exe

This script:
1. Reads version from pyproject.toml
2. Compiles main.py and dependencies to dist/availability_monitor.exe
3. Includes Playwright and all required resources
4. Creates one-file executable with hidden imports
"""

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

try:
    import toml
except ImportError:
    print("ERROR: toml not installed. Run: pip install toml")
    sys.exit(1)


def get_version_from_pyproject() -> str:
    """Extract version from pyproject.toml."""
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    if not pyproject_path.exists():
        raise FileNotFoundError(f"pyproject.toml not found at {pyproject_path}")
    
    with open(pyproject_path) as f:
        config = toml.load(f)
    
    version = config.get("project", {}).get("version", "0.1.0")
    print(f"[OK] Version from pyproject.toml: {version}")
    return version


def get_hidden_imports() -> list:
    """Get list of hidden imports for PyInstaller."""
    return [
        "playwright",
        "playwright.sync_api",
        "playwright._impl._driver",
        "greenlet",
        "pyee",
        "pyee.base",
        "fastapi",
        "uvicorn",
        "uvicorn.lifespan",
        "uvicorn.loops",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.websockets",
        "uvicorn.middleware",
        "uvicorn.middleware.cors",
        "pydantic",
        "pydantic_core",
        "dotenv",
        "requests",
        "urllib3",
        "bs4",
        "chardet",
    ]


def build_exe(version: str, output_dir: Optional[str] = None):
    """Build executable using PyInstaller."""
    if output_dir is None:
        output_dir = str(Path(__file__).parent.parent / "dist")
    
    project_root = Path(__file__).parent.parent
    main_script = project_root / "main.py"
    
    if not main_script.exists():
        raise FileNotFoundError(f"main.py not found at {main_script}")
    
    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Build PyInstaller command
    hidden_imports = get_hidden_imports()
    hidden_imports_args = " ".join([f"--hidden-import={imp}" for imp in hidden_imports])
    
    # Prepare .env file for PyInstaller (if exists)
    env_file = project_root / ".env"

    dashboard_html = project_root / "src" / "availability_alert" / "web" / "index.html"
    if not dashboard_html.exists():
        raise FileNotFoundError(f"Dashboard HTML not found at {dashboard_html}")

    pyinstaller_executable = shutil.which("pyinstaller")
    if pyinstaller_executable is None:
        venv_bin = Path(sys.executable).resolve().parent
        candidate_paths = [
            venv_bin / "pyinstaller.exe",
            venv_bin / "pyinstaller",
            Path(sys.executable).resolve().parent.parent / "Scripts" / "pyinstaller.exe",
        ]
        for candidate in candidate_paths:
            if candidate.exists():
                pyinstaller_executable = str(candidate)
                break

    if pyinstaller_executable is None:
        raise RuntimeError("PyInstaller executable not found in PATH or active virtual environment.")

    # Build PyInstaller command as array for subprocess (handles paths/spaces correctly)
    cmd_parts = [
        pyinstaller_executable,
        "--onefile",
        "--windowed",
        "--name", "availability_monitor",
        "--distpath", output_dir,
        "--specpath", str(Path(output_dir) / ".specs"),
        "--workpath", str(Path(output_dir) / ".work"),
        "--add-data", f"{dashboard_html};src/availability_alert/web",
    ]
    
    # Add hidden imports
    for imp in hidden_imports:
        cmd_parts.append(f"--hidden-import={imp}")
    
    # Add collect-all arguments
    cmd_parts.extend([
        "--collect-all=playwright",
        "--collect-all=greenlet",
        "--collect-all=pyee",
        "--collect-all=fastapi",
        str(main_script)
    ])
    
    print(f"Building executable...")
    print(f"PyInstaller executable: {pyinstaller_executable}")
    print(f"PyInstaller arguments: {cmd_parts}")
    
    result = subprocess.run(cmd_parts, cwd=str(project_root))
    
    if result.returncode != 0:
        raise RuntimeError(f"PyInstaller build failed with code {result.returncode}")
    
    exe_path = Path(output_dir) / "availability_monitor.exe"
    
    if exe_path.exists():
        file_size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"[OK] Executable built: {exe_path}")
        print(f"  Size: {file_size_mb:.1f} MB")
        return str(exe_path)
    else:
        raise RuntimeError(f"Executable not found at {exe_path}")


def verify_executable(exe_path: str):
    """Verify the executable works by running it with --help."""
    try:
        result = subprocess.run(
            [exe_path, "--help"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print(f"[OK] Executable verification passed")
            return True
        else:
            print(f"[WARNING] Executable returned code {result.returncode}")
            print(f"  stdout: {result.stdout[:200]}")
            print(f"  stderr: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"[WARNING] Could not verify executable: {e}")
        return False


def main():
    """Main build entry point."""
    print("=" * 60)
    print("Availability Monitor - PyInstaller Build Script")
    print("=" * 60)
    
    try:
        # Get version
        version = get_version_from_pyproject()
        
        # Build executable
        exe_path = build_exe(version)
        
        # Verify
        verify_executable(exe_path)
        
        print("\n" + "=" * 60)
        print(f"[OK] Build successful: {exe_path}")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n[FAILED] Build failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
