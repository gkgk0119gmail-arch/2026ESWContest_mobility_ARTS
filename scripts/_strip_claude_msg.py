#!/usr/bin/env python3
"""커밋 메시지에서 Claude 귀속 줄만 지운다 (git filter-branch --msg-filter 용).
본문은 건드리지 않고, 끝에 남는 빈 줄도 정리한다."""
import sys, re
PAT = re.compile(r"co-authored-by:\s*claude|generated with .*claude code|🤖", re.I)
lines = [l for l in sys.stdin.read().splitlines() if not PAT.search(l)]
while lines and not lines[-1].strip():
    lines.pop()
sys.stdout.write("\n".join(lines) + "\n")
