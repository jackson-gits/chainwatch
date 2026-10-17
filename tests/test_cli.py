"""CLI tests: invoke via chainwatch.cli.main([...]) in-process (spec 18.2): --help exit codes,
cheap end-to-end commands, refused/refused exit codes."""
from __future__ import annotations

from chainwatch import cli


def test_cli_help_returns_ok():
    # argparse --help raises SystemExit(0); main catches it and returns EXIT_OK
    assert cli.main(["--help"]) == cli.EXIT_OK


def test_cli_no_command_is_usage_error():
    # subparsers are required -> SystemExit(2) -> EXIT_USAGE
    assert cli.main([]) == cli.EXIT_USAGE


def test_cli_unknown_command_is_usage_error():
    assert cli.main(["nope"]) == cli.EXIT_USAGE


def test_cli_bad_option_is_usage_error():
    assert cli.main(["--nonexistent"]) == cli.EXIT_USAGE


def test_cli_world_freeze_with_no_worlds(tmp_workdir, monkeypatch):
    # freeze_worlds(None) globs data/processed/world_* (usually empty) and writes an
    # empty manifest: cheap, exit 0. It writes results/world_manifest.json in the repo.
    from chainwatch.config import ROOT

    results = ROOT / "results"
    results.mkdir(parents=True, exist_ok=True)
    target = results / "world_manifest.json"
    backup = None
    if target.exists():
        backup = target.read_bytes()
    try:
        rc = cli.main(["world-freeze"])
        assert rc == cli.EXIT_OK
        import json

        man = json.loads(target.read_text(encoding="utf-8"))
        assert "note" in man
        assert "worlds" in man
    finally:
        if backup is not None:
            target.write_bytes(backup)
        elif target.exists():
            target.unlink()


def test_cli_check_claims_exit_code():
    # may pass or fail depending on rendered docs; either way it is a real exit code
    rc = cli.main(["check-claims"])
    assert rc in (cli.EXIT_OK, cli.EXIT_FAIL)


def test_cli_parser_exposes_commands():
    parser = cli.build_parser()
    actions = [a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"]
    assert actions
    sub = actions[0].choices
    for name in ("world", "features", "train", "thresholds", "evaluate", "leakage", "report", "serve"):
        assert name in sub


def test_cli_evaluate_missing_required_is_usage_error():
    # argparse missing --features/--models raises SystemExit(2)
    assert cli.main(["evaluate"]) == cli.EXIT_USAGE


def test_cli_refused_paths_use_exit_code_3(monkeypatch):
    # a Refused exception raised by a command maps to EXIT_REFUSED (spec 18.2)
    def bad_fn(args):
        raise cli.Refused("nope")

    monkeypatch.setattr(cli, "cmd_world_freeze", bad_fn)
    rc = cli.main(["world-freeze"])
    assert rc == cli.EXIT_REFUSED


def test_cli_generic_failure_maps_to_exit_1(monkeypatch):
    def bad_fn(args):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "cmd_world_freeze", bad_fn)
    rc = cli.main(["world-freeze"])
    assert rc == cli.EXIT_FAIL
