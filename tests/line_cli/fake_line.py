"""A fake `line` executable driven by a JSON scenario (stdlib only).

Env:
  FAKE_LINE_SCENARIO  path to the scenario JSON
Scenario: {"rules": [{"args": [...] | "prefix": [...], "responses": [
              {"exit": 0, "stdout": <str|json>, "stderr": "", "sleep": 0}]}]}
A response may carry `"write": {"b64": "<base64>", "pad": N}` to create the `--output` file
(exclusively, like the real CLI) with those bytes followed by N zero bytes.
The last response of a rule repeats. Every call is appended to `<scenario>.log`.
"""

import base64
import json
import os
import sys
import time


def main() -> int:
    scenario_path = os.environ["FAKE_LINE_SCENARIO"]
    with open(scenario_path, encoding="utf-8") as handle:
        scenario = json.load(handle)
    args = sys.argv[1:]
    stdin = sys.stdin.buffer.read().decode("utf-8", errors="replace")

    with open(scenario_path + ".log", "a", encoding="utf-8") as log:
        log.write(json.dumps({"args": args, "stdin": stdin}, ensure_ascii=False) + "\n")

    state_path = scenario_path + ".state"
    try:
        with open(state_path, encoding="utf-8") as handle:
            state = json.load(handle)
    except FileNotFoundError:
        state = {}

    response = {"exit": 1, "stdout": "", "stderr": "line: unknown command; run line help"}
    for index, rule in enumerate(scenario.get("rules", [])):
        if "args" in rule and rule["args"] != args:
            continue
        if "prefix" in rule and args[: len(rule["prefix"])] != rule["prefix"]:
            continue
        responses = rule.get("responses") or [rule]
        hit = state.get(str(index), 0)
        state[str(index)] = hit + 1
        response = responses[min(hit, len(responses) - 1)]
        break
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)

    spec = response.get("write")
    if spec and "--output" in args:
        target = args[args.index("--output") + 1]
        payload = base64.b64decode(spec.get("b64", "")) + b"\0" * int(spec.get("pad", 0))
        try:
            with open(target, "xb") as handle:
                handle.write(payload)
        except FileExistsError:
            sys.stderr.write("line: output path already exists\n")
            return 1

    if response.get("sleep"):
        time.sleep(response["sleep"])
    out = response.get("stdout", "")
    if not isinstance(out, str):
        out = json.dumps(out, ensure_ascii=False)
    sys.stdout.buffer.write(out.encode("utf-8"))
    sys.stdout.flush()
    sys.stderr.buffer.write(response.get("stderr", "").encode("utf-8"))
    sys.stderr.flush()
    return int(response.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
