"""Trusted, administrator-provisioned industrial validator adapter.

No PDF/X declaration or operator checkbox substitutes for validator evidence.
The executable receives one job.json path and returns one JSON object on stdout.
"""
import json
import subprocess
from pathlib import Path

REQUIRED_CHECKS = {"pdfx", "color_icc", "effective_resolution", "page_boxes",
                   "safe_areas", "template_geometry", "barcode_isbn", "font_license"}


def validate_industrial(executable, job_path, expected_hashes):
    if not executable:
        return {
            "tool_version": "",
            "pdfx_standard": "",
            "checks": [{"severity": "BLOCKER", "code": "industrial.unavailable",
                        "message": "Validador industrial não configurado: PDF/X, ICC, imagens, caixas, áreas seguras, template, código de barras e licença das fontes pendentes."}],
        }
    executable = Path(executable)
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError("Configure um executável absoluto para o validador industrial.")
    result = subprocess.run([str(executable), str(job_path)], capture_output=True, text=True, timeout=120, check=False)
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise ValueError("O validador industrial falhou ou excedeu o limite de resposta.")
    report = json.loads(result.stdout)
    if report.get("hashes") != expected_hashes or not report.get("tool_version"):
        raise ValueError("Evidência industrial sem versão ou hashes correspondentes.")
    checks = report.get("checks", [])
    if not isinstance(checks, list) or not REQUIRED_CHECKS.issubset({c.get("code") for c in checks}):
        raise ValueError("Relatório industrial incompleto.")
    if any(c.get("severity") not in {"INFO", "WARNING", "ERROR", "BLOCKER"} or not c.get("message") for c in checks):
        raise ValueError("Severidade industrial inválida.")
    # PDF/X only passes on explicit named standard verification.
    pdfx = next(c for c in checks if c["code"] == "pdfx")
    if pdfx["severity"] == "INFO" and report.get("pdfx_standard") not in {"PDF/X-1a:2001", "PDF/X-3:2002"}:
        raise ValueError("Padrão PDF/X não verificado para este perfil.")
    return {
        "tool_version": report["tool_version"],
        "pdfx_standard": report.get("pdfx_standard", ""),
        "checks": checks,
    }
