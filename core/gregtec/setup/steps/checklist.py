"""Step 90, the checklist (plan 7.14 rule 8, QA dashboard rule 8): ``gos doctor``'s checks of the
saved gos.toml, the keyring and the voice settings, with a re-run. Setup can be finished once no
row is an error."""

from __future__ import annotations

from collections.abc import Mapping

from gregtec import doctor
from gregtec.llm import keys
from gregtec.setup.registry import OK, WARN, Check, Context, Field, Outcome, Step
from gregtec.setup.steps import paths, voice


def rows(ctx: Context) -> list[Check]:
    # Every Play GOS knows of, so finishing refuses Export or Lab at the Play the running core
    # syncs against too (final check round 3).
    out = doctor.checklist(ctx.cfg, plays=paths.plays(ctx))
    if (row := paths.config_row(ctx)) is not None:
        out.append(row)
    try:
        backend = ", ".join(keys.backend_chain())
        out.append(Check("keyring", "Keyring", OK, backend))
    except Exception as exc:  # noqa: BLE001 - any keyring failure, said plainly
        # A warning, like a missing secret: with no keyring GOS runs in no-AI mode (D164).
        out.append(
            Check(
                "keyring",
                "Keyring",
                WARN,
                f"unavailable ({type(exc).__name__}): no key can be stored, so no AI",
            )
        )
    for name, label, missing in (
        (
            keys.ANTHROPIC,
            "Anthropic API key",
            "not stored: GOS runs in no-AI mode (plan 7.16)",
        ),
        (keys.DISCORD, "Discord bot token", "not stored: needed from WO-9.1 (Discord)"),
    ):
        stored = ctx.has_secret(name)
        out.append(
            Check(
                f"secret.{name}",
                label,
                OK if stored else WARN,
                "stored" if stored else missing,
            )
        )
    table = ctx.data.get("voice")
    out += voice.readback(table if isinstance(table, Mapping) else {})
    return out


def check(ctx: Context, values: Mapping[str, str]) -> Outcome:
    return Outcome(checks=rows(ctx))


STEPS = [
    Step(
        id="checklist",
        title="Checklist",
        order=90,
        intro=(
            "gos doctor's checks of what you saved. Fix any row marked with an error, then "
            "re-run; warnings can wait. Finish marks setup done; the wizard stays in Settings."
        ),
        fields=(Field("doctor", "gos doctor", "checks"),),
        check=check,
    )
]
