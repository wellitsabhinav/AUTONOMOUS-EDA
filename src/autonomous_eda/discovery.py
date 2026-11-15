"""Inspect an explicit source list by elaborating it with Yosys."""

import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from .manifest import RTLManifest, sha256
from .rtl_inventory import summarize


def publish(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".aeda-rtl-", dir=destination.parent) as temp:
        staged = Path(temp) / destination.name
        shutil.copyfile(source, staged)
        staged.replace(destination)


def inspect_rtl(
    manifest: RTLManifest,
    output: Path | None = None,
    *,
    timeout: int = 120,
    translated: Path | None = None,
) -> dict[str, object]:
    yosys = shutil.which("yosys")
    if yosys is None:
        raise ValueError("Yosys is required for RTL inspection")
    with TemporaryDirectory() as workdir:
        work = Path(workdir)
        root = work / "inputs"
        root.mkdir()
        design_root = manifest.root or manifest.sources[0].parent
        files = manifest.inputs()
        destinations = [path.resolve() for path in (output, translated) if path is not None]
        if len(set(destinations)) != len(destinations):
            raise ValueError("RTL outputs need distinct paths")
        if set(destinations) & {design_root.joinpath(path).resolve() for path in files}:
            raise ValueError("an RTL output would overwrite an input")
        if translated is not None and manifest.frontend != "sv2v":
            raise ValueError("--translated-verilog requires an sv2v frontend")
        fingerprint = manifest.fingerprint(files)
        for relative in files:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(design_root / relative, destination)
        if any(sha256(root / path) != digest for path, digest in files.items()):
            raise ValueError("design input changed during staging")
        inputs = [f"./{source.relative_to(design_root).as_posix()}" for source in manifest.sources]
        arguments = [
            f"-I./{folder.relative_to(design_root).as_posix()}" for folder in manifest.include_dirs
        ]
        arguments.extend(
            f"-D{name}={value}" if value else f"-D{name}" for name, value in manifest.defines
        )
        conversion = None
        if manifest.frontend == "sv2v":
            sv2v = shutil.which("sv2v")
            if sv2v is None:
                raise ValueError("sv2v is required for this manifest's SystemVerilog frontend")
            argv = [f"--top={manifest.top}", *arguments, *inputs]
            version = subprocess.check_output(
                [sv2v, "--version"], timeout=10, text=True, encoding="utf-8"
            ).strip()
            converted = work / "converted.v"
            with converted.open("wb") as stream:
                translation = subprocess.run(
                    [sv2v, *argv], cwd=root, stdout=stream, stderr=subprocess.PIPE, timeout=timeout
                )
            if translation.returncode or not converted.stat().st_size:
                detail = translation.stderr.decode("utf-8", errors="replace").strip()
                raise ValueError(f"sv2v translation failed: {detail}")
            conversion = {
                "generator": "sv2v",
                "version": version,
                "executable_sha256": sha256(Path(sv2v)),
                "arguments": argv,
                "input_manifest_sha256": fingerprint,
                "output": converted.name,
                "output_sha256": sha256(converted),
            }
            arguments, inputs = [], ["../converted.v"]
        elif manifest.language == "systemverilog":
            arguments.insert(0, "-sv")
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
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError(f"Yosys elaboration failed: {result.stderr.strip() or result.stdout}")
        netlist = work / "design.json"
        modules = json.loads(netlist.read_text(encoding="utf-8"))["modules"]
        if manifest.top not in modules or not modules[manifest.top].get("ports"):
            raise ValueError("elaborated top must have at least one port")
        if output is not None:
            publish(netlist, output)
        if translated is not None:
            publish(work / "converted.v", translated)
        netlist_sha256 = sha256(netlist)

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
        "netlist_sha256": netlist_sha256,
        "conversion": conversion,
        "constraints": {
            "status": "AVAILABLE" if manifest.constraints else "MISSING",
            "files": [path.relative_to(design_root).as_posix() for path in manifest.constraints],
        },
        "readiness": summarize(manifest.top, modules, elaborated=manifest.synthesis == "elaborate"),
    }
