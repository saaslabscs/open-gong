"""`open-gong` CLI — first-run onboarding.

    uv run python -m app.cli doctor          # show what's configured
    uv run python -m app.cli init            # interactive setup
    uv run python -m app.cli init --pyai-key pyai_...   # non-interactive
    uv run python -m app.cli init --no-pyai  # stay on the offline mock

`init` tries to mint a PyAI sandbox key with no auth (confirmed available);
if the network is over quota it falls back to pasting a key, or the offline
mock so the demo still runs with zero keys.
"""

import argparse
import sys

from . import config  # noqa: F401 — load .env first
from .setup_env import mint_sandbox_key, read_env, set_env, status


def _print_status() -> None:
    s = status()
    def mark(ok):  # noqa: E306
        return "\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m"
    print("\nOpen Gong — configuration")
    print(f"  {mark(s['llm']['ok'])} LLM: {s['llm']['detail']}")
    print(f"  {mark(s['transcription']['ok'])} Transcription: {s['transcription']['detail']}")
    ready = s["can_process_uploads"]
    print(f"\n  {mark(ready)} " + (
        "Ready to process your own uploads." if ready
        else "Samples work; add the missing key above to process new uploads."
    ))


def cmd_doctor(_args) -> int:
    _print_status()
    return 0


def cmd_init(args) -> int:
    env = read_env()
    interactive = sys.stdin.isatty() and not (args.pyai_key or args.no_pyai)

    # 1. LLM key
    provider = env.get("LLM_PROVIDER", "anthropic")
    llm_key_name = "OPENROUTER_API_KEY" if provider == "openrouter" else "ANTHROPIC_API_KEY"
    if not env.get(llm_key_name):
        if args.llm_key:
            set_env({llm_key_name: args.llm_key})
        elif interactive:
            v = input(f"Paste your {llm_key_name} (or Enter to skip): ").strip()
            if v:
                set_env({llm_key_name: v})
        else:
            print(f"! {llm_key_name} not set — insights won't run until it is.")

    # 2. PyAI transcription
    if args.no_pyai:
        set_env({"PYAI_ADAPTER": "mock"})
        print("Staying on the offline mock adapter (samples only).")
    elif args.pyai_key:
        set_env({"PYAI_API_KEY": args.pyai_key, "PYAI_ADAPTER": "real"})
        print("PyAI key saved; live transcription enabled.")
    elif not env.get("PYAI_API_KEY"):
        want = "y" if not interactive else input(
            "Enable live transcription via PyAI? Tries a free sandbox key. [Y/n] "
        ).strip().lower()
        if want in ("", "y", "yes"):
            print("Minting a PyAI sandbox key…")
            res = mint_sandbox_key()
            if res["ok"]:
                set_env({"PYAI_API_KEY": res["key"], "PYAI_ADAPTER": "real"})
                print("✓ Sandbox key minted and saved; live transcription enabled.")
            else:
                print(f"! Could not auto-mint: {res['reason']}")
                v = input("Paste a PyAI key (or Enter to stay on mock): ").strip() if interactive else ""
                if v:
                    set_env({"PYAI_API_KEY": v, "PYAI_ADAPTER": "real"})
                    print("✓ PyAI key saved; live transcription enabled.")
                else:
                    set_env({"PYAI_ADAPTER": "mock"})
                    print("Staying on the offline mock adapter.")

    _print_status()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="open-gong")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="show configuration status")
    pi = sub.add_parser("init", help="first-run setup")
    pi.add_argument("--pyai-key", help="set PyAI key non-interactively")
    pi.add_argument("--llm-key", help="set the LLM provider key non-interactively")
    pi.add_argument("--no-pyai", action="store_true", help="stay on the offline mock")
    args = p.parse_args()
    return {"doctor": cmd_doctor, "init": cmd_init}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
