"""One recorded resolution for the sixteen host examples, and the check that it holds.

The examples exist to *measure* framework behaviour: whether Flask's response
iterator ``close()`` runs before the host releases the request, whether Quart's
``test_client()`` supports ``async with``, whether an injected Typer parameter
becomes a positional argument. Each of those answers is decided by code the
example does not pin directly -- the framework's transitive dependencies. The
T14-08 failure showed what that costs: Typer does not constrain Click, so an
unpinned Click silently decided whether a parameter raised at build time or
became a positional argument, and the test had recorded a ``RuntimeError`` that
no installed version raises. A green test whose meaning depends on an implicit
resolution is a test whose meaning is unknown.

So the resolution is written down. ``constraints.txt`` next to this module is the
complete closure of all sixteen apps' ``requirements.txt`` files, measured from a
real install; ``nox -s examples`` installs with ``-c constraints.txt`` and then
runs :func:`main` with ``--verify``, so the venv that runs the suite is the one
the file describes. The check below is the part that can fail:

* every app's own pins agree with ``constraints.txt`` (an app pin that disagrees
  with the shared resolution is the two-versions-in-one-venv defect);
* no two apps require different versions of the same distribution;
* the closure of every app's pins -- computed from the *installed* metadata, so
  the requirement graph the frameworks actually declare -- is pinned exactly in
  ``constraints.txt``, and ``constraints.txt`` pins nothing the apps cannot
  reach (a stale entry is a pin nobody measures);
* every pinned distribution is installed at its pinned version.

The closure needs the hosts installed to compute, so a bare checkout skips that
half with a reason instead of reporting drift it cannot see. Nothing here imports
a framework: only the distribution metadata is read.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from importlib import metadata

from packaging.requirements import Requirement

APPS = pathlib.Path(__file__).resolve().parent
MANIFEST = APPS / "apps.json"
CONSTRAINTS = APPS / "constraints.txt"

#: ``name==version`` -- the only form an example requirement or a constraint may take.
PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==(?P<version>[^\s;,]+)$")
_NORMALISE_RE = re.compile(r"[-_.]+")


def canonical(name: str) -> str:
    """A distribution name in the form that compares equal across spellings.

    ``PyYAML``, ``pyyaml`` and ``Py_YAML`` are one distribution (PEP 503), and the
    metadata API insists on this normalisation of its own.
    """
    return _NORMALISE_RE.sub("-", name).strip().lower()


def read_pins(path: pathlib.Path) -> dict[str, str]:
    """The exact pins in ``path``, keyed by canonical name.

    Comments are stripped, so the reason a pin exists can sit on the same line as
    the pin. A line that is not an exact pin raises rather than being ignored: a
    range in this file would restore the floating resolution the file exists to
    remove.
    """
    pins: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        match = PIN_RE.match(entry)
        if match is None:
            raise ValueError(f"{path}:{number}: not an exact `name==version` pin: {entry!r}")
        pins[canonical(match.group("name"))] = match.group("version")
    return pins


def app_names() -> list[str]:
    """Every app the manifest lists, in manifest order."""
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [app["name"] for app in document["apps"]]


def app_pins(app: str) -> dict[str, str]:
    """The host distributions one app pins itself."""
    return read_pins(APPS / app / "requirements.txt")


def constraints() -> dict[str, str]:
    """The shared resolution every app is measured against."""
    return read_pins(CONSTRAINTS)


def installed(name: str) -> str | None:
    """The installed version of ``name``, or ``None`` when it is not installed."""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def closure(pins: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Every distribution ``pins`` reaches, with the version that is installed.

    ``Requires-Dist`` is read from the installed distribution rather than from an
    index, so this is the graph the venv actually has -- the same one the tests
    run against. Markers are evaluated with this interpreter's environment, so
    ``python_version < "3.11"`` and ``sys_platform`` extras drop out the way pip
    drops them. A pin that is not installed is reported separately: the closure of
    a distribution that is absent cannot be known, and guessing it is what this
    module exists to avoid.

    Returns ``(reached, unresolved)`` where ``reached`` maps canonical name to the
    installed version and ``unresolved`` names the pins that are not installed.
    """
    reached: dict[str, str] = {}
    unresolved: list[str] = []
    stack = list(pins)
    while stack:
        name = stack.pop()
        if name in reached or name in unresolved:
            continue
        version = installed(name)
        if version is None:
            unresolved.append(name)
            continue
        reached[name] = version
        for raw in metadata.requires(name) or ():
            requirement = Requirement(raw)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            stack.append(canonical(requirement.name))
    return reached, sorted(unresolved)


def reachable_by() -> dict[str, list[str]]:
    """``constraints`` entry -> the apps whose closure needs it."""
    attribution: dict[str, list[str]] = {}
    for app in app_names():
        reached, _ = closure(app_pins(app))
        for name in reached:
            attribution.setdefault(name, []).append(app)
    return {name: sorted(apps) for name, apps in attribution.items()}


def problems(*, require_installed: bool = False) -> list[str]:
    """Every way the recorded resolution and the apps disagree.

    ``require_installed`` turns "the hosts are absent, so the closure cannot be
    computed" from a skip into a failure -- the ``examples`` session passes it,
    because there the hosts have just been installed and an absent one means the
    install did not do what the session claims.
    """
    found: list[str] = []
    shared = constraints()

    per_app: dict[str, dict[str, str]] = {app: app_pins(app) for app in app_names()}
    for app, pins in per_app.items():
        for name, version in sorted(pins.items()):
            recorded = shared.get(name)
            if recorded is None:
                found.append(
                    f"examples/apps/{app}/requirements.txt pins {name}=={version}, which "
                    f"examples/apps/constraints.txt does not record"
                )
            elif recorded != version:
                found.append(
                    f"examples/apps/{app}/requirements.txt pins {name}=={version}, but "
                    f"examples/apps/constraints.txt records {recorded}"
                )

    versions: dict[str, dict[str, list[str]]] = {}
    for app, pins in per_app.items():
        for name, version in pins.items():
            versions.setdefault(name, {}).setdefault(version, []).append(app)
    for name, by_version in sorted(versions.items()):
        if len(by_version) > 1:
            detail = "; ".join(
                f"{version} from {', '.join(sorted(apps))}"
                for version, apps in sorted(by_version.items())
            )
            found.append(
                f"{name} is pinned at {len(by_version)} versions across the apps ({detail}) — "
                "the shared `examples` venv can only install one of them"
            )

    reachable: dict[str, set[str]] = {}
    unresolved: list[str] = []
    for app, pins in per_app.items():
        reached, missing = closure(pins)
        unresolved.extend(f"{name} (from {app})" for name in missing)
        for name, version in reached.items():
            reachable.setdefault(name, set()).add(app)
            recorded = shared.get(name)
            if recorded is None:
                found.append(
                    f"examples/apps/constraints.txt does not pin {name}, which "
                    f"examples/apps/{app}/requirements.txt reaches at {version}"
                )
            elif recorded != version:
                found.append(
                    f"examples/apps/{app}/requirements.txt reaches {name}=={version} through "
                    f"its dependencies, but examples/apps/constraints.txt records {recorded}"
                )
    if unresolved and require_installed:
        found.append(
            "the closure cannot be computed because these pins are not installed: "
            + ", ".join(sorted(set(unresolved)))
        )
    elif not reachable:
        return found  # hosts absent: nothing else can be measured from here

    for name in sorted(set(shared) - set(reachable)):
        found.append(
            f"examples/apps/constraints.txt pins {name}=={shared[name]}, which no app reaches — "
            "regenerate it with `python examples/apps/_pins.py --write`"
        )

    for name, version in sorted(shared.items()):
        seen = installed(name)
        if seen is not None and seen != version:
            found.append(
                f"{name}=={version} is pinned but {seen} is installed — the venv is not the "
                "resolution the evidence was recorded on"
            )
    return found


def measured() -> tuple[dict[str, str], list[str]]:
    """The resolution the installed venv actually has: every app's closure, merged.

    Returns ``(pins, unresolved)``. ``unresolved`` is empty only when every pin in
    every app resolved to an installed distribution, which is the condition for
    recording a resolution at all.
    """
    shared: dict[str, str] = {}
    unresolved: list[str] = []
    conflicts: list[str] = []
    for app in app_names():
        reached, missing = closure(app_pins(app))
        unresolved.extend(missing)
        for name, version in reached.items():
            existing = shared.setdefault(name, version)
            if existing != version:
                conflicts.append(f"{name}: {existing} and {version} (from {app})")
    return shared, sorted(set(unresolved)) + conflicts


#: Why the file exists, kept above the pins so a reader meets the reason first.
HEADER = """\
# The sixteen example apps' resolved dependency closure (PRDs 10-14).
#
# `examples/apps/<app>/requirements.txt` names the host each example is about;
# this file names everything that host *brings with it*, because that is what
# decides the behaviour the tests assert -- Werkzeug's response `close()`,
# Click's argument parsing under Typer, aiohttp's `multidict` headers. An
# unpinned transitive is how the T14-08 claim was silently rewritten once.
#
# `nox -s examples` installs with `-c` this file and then runs
# `python examples/apps/_pins.py --verify`, so the venv that runs the suite is
# the one described here; `tests/examples/test_apps_manifest.py` checks the same
# relation from a bare checkout. Regenerate, from the venv that installed the
# hosts, with:
#
#     python examples/apps/_pins.py --write
#
# Each line names the apps whose closure reaches it. A pin no app reaches is a
# pin nobody measures, and both `--verify` and the test report it.
"""


def render(shared: dict[str, str], attribution: dict[str, list[str]]) -> str:
    """The file text for an explicit resolution.

    Attribution matters more than alphabetical neatness: when a pin blocks an
    upgrade the first question is which example measures it, and a trailing
    comment answers that without a second tool.
    """
    body = []
    for name in sorted(shared):
        apps = attribution.get(name, [])
        reason = ", ".join(apps) if apps else "unreached — remove"
        body.append(f"{name}=={shared[name]}  # {reason}")
    return HEADER + "\n" + "\n".join(body) + "\n"


def write() -> int:
    """Rewrite ``constraints.txt`` from the installed closure.

    The pins come from what is installed, so this must run in the venv
    ``nox -s examples`` built: run anywhere else and it records that
    interpreter's resolution instead, which is the mistake the file prevents.
    """
    shared, unresolved = measured()
    if unresolved:
        print(
            "error: the resolution cannot be recorded — not installed, or in conflict: "
            + ", ".join(unresolved),
            file=sys.stderr,
        )
        return 1
    CONSTRAINTS.write_text(render(shared, reachable_by()), encoding="utf-8")
    print(f"wrote examples/apps/constraints.txt: {len(shared)} pin(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write", action="store_true", help="rewrite constraints.txt from the installed closure"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="fail when the apps, the constraints and the venv disagree (the nox session)",
    )
    args = parser.parse_args(argv)

    if args.write:
        return write()
    if not CONSTRAINTS.is_file():
        print(
            "examples/apps/constraints.txt is missing; record the resolution with "
            "`python examples/apps/_pins.py --write` in the venv that installed the hosts",
            file=sys.stderr,
        )
        return 1

    found = problems(require_installed=args.verify)
    if found:
        print("the example apps' dependency resolution does not hold:", file=sys.stderr)
        for line in found:
            print(f"  - {line}", file=sys.stderr)
        return 1
    print(
        f"examples/apps/constraints.txt: {len(constraints())} pin(s) agree with "
        f"{len(app_names())} app(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
