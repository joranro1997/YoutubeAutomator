"""Verify the prproj_xml traversal against known describe data + save round-trip."""
from pathlib import Path
import sys

from youtube_automator.adobe.prproj_xml import Project

TPL = Path("assets/premiere_templates/lom_nest.prproj")
SEQ = "2023-03-23 20-59-52"


def show(p: Project) -> dict:
    m = p.map_sequence(SEQ)
    for lbl in sorted(m, key=lambda x: (x[0], int(x[1:]))):
        cs = m[lbl]
        rows = [
            (c.name, round(c.start_sec or 0, 1), round(c.end_sec or 0, 1),
             round(c.in_sec, 1) if c.in_sec is not None else None,
             round(c.out_sec, 1) if c.out_sec is not None else None)
            for c in cs[:5]
        ]
        print(f"{lbl}: {len(cs)} clips {rows}")
    return m


def main() -> int:
    print("== load lom_nest.prproj ==")
    p = Project.load(TPL)
    m = show(p)

    print("\n== save round-trip (no mutation) ==")
    out = Path("data/tmp/_roundtrip.prproj")
    p.save(out)
    p2 = Project.load(out)
    m2 = p2.map_sequence(SEQ)
    same = sorted(m) == sorted(m2) and all(
        len(m[k]) == len(m2[k]) for k in m
    )
    print(f"round-trip track/clip counts identical: {same}")
    out.unlink(missing_ok=True)
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
