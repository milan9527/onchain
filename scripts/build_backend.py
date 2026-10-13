"""Build a reproducible arm64 Python bundle for Lambda and AgentCore direct-code deployment."""
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
build = ROOT / ".build"
package = build / "python"
build.mkdir(exist_ok=True)
if package.exists():
    shutil.rmtree(package)
package.mkdir()
requirements = ROOT / "requirements.lock"
if not requirements.exists():
    raise SystemExit("Generate requirements.lock before building.")
subprocess.run([
    sys.executable, "-m", "pip", "install", "--quiet", "--target", str(package),
    "--platform", "manylinux2014_aarch64", "--implementation", "cp", "--python-version", "3.12",
    "--only-binary=:all:", "-r", str(requirements),
], check=True)
shutil.copytree(ROOT / "backend" / "onchain", package / "onchain", dirs_exist_ok=True)
shutil.copytree(ROOT / "runtimes", package / "runtimes", dirs_exist_ok=True)
archive = build / "backend.zip"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
    for file in sorted(package.rglob("*")):
        if file.is_file() and "__pycache__" not in file.parts and not file.name.endswith(".pyc"):
            z.write(file, file.relative_to(package))
manifest = {"sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "size": archive.stat().st_size}
(build / "backend-manifest.json").write_text(json.dumps(manifest, indent=2))
print(json.dumps(manifest))
