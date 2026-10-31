"""Every requirement the grader scores or enforces must be requested in the prompt and
documented in TASK_GRADER_ALIGNMENT.md. Scored fields must exist in the answer schema."""

import sys

from _common import ROOT

from scopeshift.alignment import check_alignment


def main() -> int:
    rows, problems = check_alignment(ROOT)
    for req, phrase, line in rows:
        print(f"OK   {req:<20} prompt line {line:>3}: {phrase}")
    for p in problems:
        print(f"FAIL {p}")
    print(f"alignment: {'FAIL' if problems else 'PASS'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
