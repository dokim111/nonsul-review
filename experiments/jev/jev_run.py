"""Send rubric judgments to Jev and keep every raw response.

Examples (from the repository root, inside the project virtualenv):

    # Look at the requests without sending anything
    python experiments/jev/jev_run.py --variant A --dry-run \
        --problems examples/ex-003 --answers examples/ex-003/answers --out results/jev

    # One real request, printed, to confirm the response format
    python experiments/jev/jev_run.py --variant A --probe \
        --problems examples/ex-003 --answers examples/ex-003/answers --out results/jev

    # Default provider is OpenCode Zen with the free model (key: OPENCODE_API_KEY).
    # A real run: three repeats, a hard request cap
    python experiments/jev/jev_run.py --variant A --repeat 3 --max-requests 30 \
        --problems examples/ex-002 examples/ex-003 \
        --answers examples/ex-002/answers examples/ex-003/answers --out results/jev
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jev_common import (  # noqa: E402
    LANGS,
    VARIANTS,
    Case,
    build_request,
    discover_cases,
    load_requirements,
    request_digest,
    sha256_file,
)

# Provider presets. OpenCode Zen serves the same System One contract as TypeSafe.
PROVIDERS: dict[str, dict[str, str]] = {
    "zen": {
        "endpoint": "https://opencode.ai/zen/v1/systemone",
        "model": "jev-1.13-free",  # limited-time free model
        "key_env": "OPENCODE_API_KEY",
    },
    "typesafe": {
        "endpoint": "https://api.typesafe.ai/v1/systemone",
        "model": "jev-1.13",  # pin a version; confirm the exact ID in the console
        "key_env": "TYPESAFE_API_KEY",
    },
}
FREE_MODELS = {"zen": {"jev-1.13-free"}, "typesafe": set()}
USER_AGENT = "nonsul-review-jev-pilot/0.1 (+https://github.com/dokim111/nonsul-review)"

Transport = Callable[[str, str, dict[str, Any], float], tuple[int, Any]]


def http_transport(endpoint: str, key: str, body: dict[str, Any], timeout: float):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Cloudflare-fronted gateways reject urllib's default agent (error 1010).
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(text)
        except ValueError:
            return exc.code, {"error_text": text[:2000]}


def send(
    transport: Transport,
    endpoint: str,
    key: str,
    body: dict[str, Any],
    timeout: float,
    retries: int,
    budget: int,
) -> tuple[int, Any, int, float]:
    """Send with retries. Every attempt counts against ``budget`` (real transmissions)."""
    attempts = 0
    while True:
        attempts += 1
        start = time.monotonic()
        try:
            status, payload = transport(endpoint, key, body, timeout)
        except (urllib.error.URLError, TimeoutError) as exc:
            status, payload = 0, {"transport_error": type(exc).__name__}
        latency = time.monotonic() - start
        retryable = status == 0 or status == 429 or status >= 500
        if not retryable or attempts > retries or attempts >= budget:
            return status, payload, attempts, latency
        time.sleep(min(2**attempts, 20))


def cost_status(payload: Any) -> tuple[str, float | None]:
    cost = payload.get("cost") if isinstance(payload, dict) else None
    if isinstance(cost, bool) or not isinstance(cost, (int, float)):
        return "unknown", None
    return ("zero" if cost == 0 else "charged"), float(cost)


def requirements_for(case: Case, req_dirs: list[Path]):
    for directory in req_dirs:
        path = directory / f"{case.problem.id}.yaml"
        if path.is_file():
            return load_requirements(path, case.problem), path
    return None, None


def run(argv: list[str] | None = None, transport: Transport = http_transport) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--problems", nargs="+", type=Path, required=True)
    parser.add_argument("--answers", nargs="+", type=Path, required=True)
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--lang", choices=LANGS, default="ko")
    parser.add_argument("--requirements", nargs="*", type=Path, default=[])
    parser.add_argument("--provider", choices=sorted(PROVIDERS), default="zen")
    parser.add_argument("--model", help="override the provider's default model")
    parser.add_argument("--endpoint", help="override the provider's endpoint")
    parser.add_argument(
        "--allow-paid",
        action="store_true",
        help="permit a model that is not on the provider's free list",
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--max-requests", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="write requests, send nothing")
    parser.add_argument("--probe", action="store_true", help="send one request and print it")
    parser.add_argument(
        "--allow-learner-answers",
        action="store_true",
        help="permit source: learner answers (only with consent and a checked data agreement)",
    )
    args = parser.parse_args(argv)

    if args.repeat < 1 or args.max_requests < 1:
        parser.error("--repeat and --max-requests must be positive")
    if args.variant == "C" and not args.requirements:
        parser.error("variant C needs --requirements DIR")

    preset = PROVIDERS[args.provider]
    args.model = args.model or preset["model"]
    args.endpoint = args.endpoint or preset["endpoint"]
    if args.model not in FREE_MODELS[args.provider] and not (args.allow_paid or args.dry_run):
        parser.error(
            f"model {args.model} on {args.provider} may be billed; add --allow-paid to confirm"
        )

    cases = discover_cases(args.problems, args.answers)
    if not cases:
        parser.error("no answers matched the given problems")
    blocked = [c.answer.id for c in cases if c.answer.source != "synthetic"]
    if blocked and not args.allow_learner_answers:
        parser.error(f"learner answers would leave this machine: {', '.join(blocked)}")

    key = os.environ.get(preset["key_env"], "")
    if not (args.dry_run or key):
        parser.error(f"{preset['key_env']} is not set (use --dry-run to inspect requests)")

    base = args.out / f"variant-{args.variant}" / args.lang
    sent = skipped = failed = unknown_cost = 0
    for case in cases:
        reqs, req_path = requirements_for(case, args.requirements)
        if args.variant == "C" and reqs is None:
            print(f"skip {case.answer.id}: no requirements for {case.problem.id}", file=sys.stderr)
            continue
        body = build_request(case, args.variant, args.lang, args.model, reqs)
        for k in range(1 if args.probe else args.repeat):
            target = base / f"{case.answer.id}.r{k}.json"
            if args.dry_run:
                base.mkdir(parents=True, exist_ok=True)
                (base / f"{case.answer.id}.request.json").write_text(
                    json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                break
            if target.exists() and not args.probe:
                previous = json.loads(target.read_text(encoding="utf-8")).get("meta", {})
                if (
                    previous.get("request_sha256") != request_digest(body)
                    or previous.get("model") != args.model
                    or previous.get("provider") != args.provider
                ):
                    print(
                        f"{target} was produced by a different request or model. "
                        "Use a new --out folder for changed questions, inputs or models.",
                        file=sys.stderr,
                    )
                    return 5
                skipped += 1
                continue
            if sent >= args.max_requests:
                print(f"stopped at --max-requests {args.max_requests}", file=sys.stderr)
                return 2
            status, payload, attempts, latency = send(
                transport,
                args.endpoint,
                key,
                body,
                args.timeout,
                args.retries,
                args.max_requests - sent,
            )
            sent += attempts
            billing, cost = cost_status(payload)
            record = {
                "meta": {
                    "variant": args.variant,
                    "lang": args.lang,
                    "provider": args.provider,
                    "model": args.model,
                    "endpoint_host": args.endpoint.split("/")[2] if "//" in args.endpoint else "",
                    "answer_id": case.answer.id,
                    "problem_id": case.problem.id,
                    "repeat": k,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "latency_s": round(latency, 3),
                    "http_status": status,
                    "attempts": attempts,
                    "request_sha256": request_digest(body),
                    "problem_sha256": sha256_file(case.problem_path),
                    "answer_sha256": sha256_file(case.answer_path),
                    "requirements_sha256": sha256_file(req_path) if req_path else None,
                    "usage": payload.get("usage") if isinstance(payload, dict) else None,
                    "cost": cost,
                    "cost_status": billing,
                },
                "request": body,
                "response": payload,
            }
            free = args.model in FREE_MODELS[args.provider]
            if args.probe:
                print(json.dumps(record, ensure_ascii=False, indent=2))
                print(f"cost_status: {billing}", file=sys.stderr)
                return 0 if status == 200 and not (free and billing == "charged") else 1
            if status in (404, 410) and args.model in FREE_MODELS[args.provider]:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.with_suffix(".failed.json").write_text(
                    json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    f"{args.model} returned {status}: the free period may have ended. "
                    "Stopping; nothing was sent to a paid model.",
                    file=sys.stderr,
                )
                return 3
            if status != 200:
                failed += 1
                target = target.with_suffix(".failed.json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            if free and billing == "charged":
                print(
                    f"{args.model} reported a charge of {cost}. Stopping before the next request.",
                    file=sys.stderr,
                )
                return 4
            if billing == "unknown":
                unknown_cost += 1
            time.sleep(args.sleep)
        if args.probe:
            break

    if args.dry_run:
        print(f"wrote requests to {base}")
    else:
        print(
            f"transmissions {sent}, skipped {skipped} existing, failed {failed}, "
            f"cost not reported {unknown_cost} -> {base}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
