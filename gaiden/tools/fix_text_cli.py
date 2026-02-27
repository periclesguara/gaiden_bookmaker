from __future__ import annotations

import argparse
from pathlib import Path

from gaiden.db_preflight import require_active_db
from gaiden.fix_text_v2 import fix_text_file


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="out", required=True)
    ap.add_argument("--report", dest="report", required=True)
    args = ap.parse_args()
    require_active_db()

    rep = fix_text_file(Path(args.inp), Path(args.out), Path(args.report))

    if rep.lint_after["count"] > rep.lint_before["count"] + 10:
        raise SystemExit("FIX_TEXT increased lint issues too much; abort.")

    print("[fix_text] ok:", rep.output_path)
    print("[fix_text] report:", args.report)


if __name__ == "__main__":
    main()
