"""Inspect an explicit source list by elaborating it with Yosys."""

import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from .manifest import RTLManifest, sha256


def inspect_rtl(manifest: RTLManifest, output: Path | None = None) -> dict[str, object]:
    yosys = shutil.which("yosys")
    if yosys is None:
        raise ValueError("Yosys is required for RTL inspection")
    with TemporaryDirectory() as workdir:
        work = Path(workdir)
        root = work / "inputs"
        root.mkdir()
        design_root = manifest.root or manifest.sources[0].parent
        files = manifest.inputs()
        fingerprint = manifest.fingerprint(files)
        for relative in files:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(design_root / relative, destination)
        if any(sha256(root / path) != digest for path, digest in files.items()):
            raise ValueError("design input changed during staging")
        inputs = [f"./{source.relative_to(design_root).as_posix()}" for source in manifest.sources]
        arguments = ["-sv"] if manifest.language == "systemverilog" else []
        arguments.extend(
            f"-I./{folder.relative_to(design_root).as_posix()}" for folder in manifest.include_dirs
        )
        arguments.extend(
            f"-D{name}={value}" if value else f"-D{name}" for name, value in manifest.defines
        )
        parameters = " ".join(f"-chparam {name} {value}" for name, value in manifest.parameters)
        commands = [
            f"read_verilog {' '.join([*arguments, *inputs])}",
            f"hierarchy -check -top {manifest.top} {parameters}",
            f"synth -top {manifest.top}" if manifest.synthesis == "synth" else "proc",
            "check -assert",
            "write_json ../design.json",
        ]
        (work / "inspect.ys").write_text("\n".join(commands) + "\n", encoding="utf-8")
        result = subprocess.run(
            [yosys, "-Q", "-T", "-s", "../inspect.ys"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError(f"Yosys elaboration failed: {result.stderr.strip() or result.stdout}")
        netlist = work / "design.json"
        modules = json.loads(netlist.read_text(encoding="utf-8"))["modules"]
        if manifest.top not in modules or not modules[manifest.top].get("ports"):
            raise ValueError("elaborated top must have at least one port")
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(netlist, output)

    hierarchy = {
        name: {
            "submodules": {
                instance: cell["type"]
                for instance, cell in module.get("cells", {}).items()
                if cell["type"] in modules
            },
            "cell_count": len(module.get("cells", {})),
            "ports": {
                port: {"direction": metadata["direction"], "width": len(metadata["bits"])}
                for port, metadata in module["ports"].items()
            },
        }
        for name, module in modules.items()
    }
    return {
        "top": manifest.top,
        "design": manifest.design or manifest.top,
        "language": manifest.language,
        "platform": manifest.platform,
        "source_revision": manifest.revision,
        "parameters": dict(manifest.parameters),
        "manifest_sha256": fingerprint,
        "inputs": files,
        "ordered_sources": [str(path) for path in manifest.sources],
        "modules": hierarchy,
    }
