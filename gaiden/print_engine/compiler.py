"""Local LaTeX compilation for print interiors.

The compiler is deliberately a thin adapter around system binaries.  Gaiden
keeps the source and print spec as the canonical artefacts; TeX Live can be
upgraded independently without coupling the domain layer to it.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LatexCompileResult:
    pdf_path: Path
    command: tuple[str, ...]
    stdout: str
    stderr: str


class LatexCompilerUnavailable(RuntimeError):
    pass


def compile_lualatex(tex_path: str | Path, *, output_dir: str | Path | None = None) -> LatexCompileResult:
    source = Path(tex_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)

    target_dir = Path(output_dir).resolve() if output_dir else source.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    latexmk = shutil.which("latexmk")
    lualatex = shutil.which("lualatex")
    if latexmk:
        command = (
            latexmk,
            "-lualatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-outdir={target_dir}",
            str(source),
        )
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=180)
    elif lualatex:
        command = (
            lualatex,
            "-interaction=nonstopmode",
            "-halt-on-error",
            f"-output-directory={target_dir}",
            str(source),
        )
        first = subprocess.run(command, capture_output=True, text=True, check=False, timeout=180)
        if first.returncode != 0:
            raise RuntimeError(first.stderr or first.stdout or "LuaLaTeX compilation failed")
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=180)
    else:
        raise LatexCompilerUnavailable(
            "TeX toolchain not found. Install TeX Live with lualatex (latexmk recommended)."
        )

    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "LaTeX compilation failed")

    pdf_path = target_dir / f"{source.stem}.pdf"
    if not pdf_path.is_file():
        raise RuntimeError(f"LaTeX reported success but PDF was not created: {pdf_path}")

    return LatexCompileResult(
        pdf_path=pdf_path,
        command=tuple(command),
        stdout=result.stdout,
        stderr=result.stderr,
    )
