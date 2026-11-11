"""Steps 20, 30 and 35: the Anthropic API key, the Discord bot token and the ElevenLabs API key,
in the keyring (plan 4.6): every secret ``gos key`` manages (``keys.NAMES``).

None is ever written to gos.toml or a log, or sent back to the dashboard: the wizard sees
only whether one is stored. The Anthropic key's test is one small call to the fast lane's model
through the fast lane's own client (D168): ``models.list``, the free probe no-AI mode uses,
can't see credits, and the fast lane is what has to work.
"""

from __future__ import annotations

from typing import Any

import anthropic

from gregtec.agents import fast_lane
from gregtec.agents.no_ai import CHOICE, CREDITS, LABELS, classify
from gregtec.llm import keys
from gregtec.llm.smoke import explain_api_error
from gregtec.setup.registry import Field, Step, TestEnv

TEST_MAX_TOKENS = 8
TEST_PROMPT = "Reply with the single word: ok"


def test_anthropic(env: TestEnv, key: str) -> tuple[bool, str]:
    """One Messages API call on the fast lane's model and request shape (thinking off, effort
    low), metered as ``setup-test``. Refused while the AI is off by Clayton's switch or the
    credit pause (D164: nothing spends while it is off)."""
    if env.ai is not None:
        held = [r for r in env.ai.reasons() if r in (CHOICE, CREDITS)]
        if held:
            return False, (
                f"not tested: the AI is off ({LABELS[held[0]]}); turn it on first, so "
                "nothing is spent while it is off"
            )
    model = fast_lane.HAIKU
    client: Any = env.client_factory(key)
    try:
        message = client.messages.create(
            model=model,
            max_tokens=TEST_MAX_TOKENS,
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": TEST_PROMPT}],
        )
    except anthropic.APIError as exc:
        found = env.ai.failed(exc) if env.ai is not None else classify(exc)
        if found is not None:
            return False, found.detail
        status = getattr(exc, "status_code", None)
        return False, explain_api_error(status, str(exc))[:300]
    except Exception as exc:  # noqa: BLE001 - whatever the client raises, said plainly
        return False, f"the test call failed ({type(exc).__name__})"
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    if env.ai is not None:
        env.ai.succeeded()
    if env.meter is not None:
        env.meter.record(
            fast_lane.FAST, model, fast_lane._usage(message), task="setup-test"
        )
    return True, f"the API answered on {model} (the fast lane's model)"


STEPS = [
    Step(
        id="anthropic",
        title="Anthropic API key",
        order=20,
        intro=(
            "The key from the Console organization linked to the Max plan (plan 4.4), in the "
            "gregtec workspace. It goes into Windows Credential Manager, never into gos.toml or "
            "a log, and is never shown again. Without it GOS runs in no-AI mode (plan 7.16)."
        ),
        fields=(
            Field(
                "key",
                "API key",
                "secret",
                secret=keys.ANTHROPIC,
                help="Starts with sk-ant-. Test it after storing it: one tiny fast-lane call.",
                placeholder="sk-ant-...",
                tester=test_anthropic,
            ),
        ),
    ),
    Step(
        id="discord",
        title="Discord bot token",
        order=30,
        intro=(
            "The token of your own Discord bot (alerts and quick questions, WO-9.1). Stored in "
            "Windows Credential Manager and never shown again. You can add it later."
        ),
        fields=(
            Field(
                "token",
                "Bot token",
                "secret",
                secret=keys.DISCORD,
                help="Discord developer portal > your app > Bot > Reset Token",
            ),
        ),
        optional=True,
    ),
    Step(
        id="elevenlabs",
        title="ElevenLabs API key",
        order=35,
        intro=(
            'Only for the optional cloud voice ([voice] tts = "elevenlabs", D203); the local '
            "voices need no key. Stored in Windows Credential Manager and never shown again. "
            "You can add it later."
        ),
        fields=(
            Field(
                "key",
                "API key",
                "secret",
                secret=keys.ELEVENLABS,
                help="elevenlabs.io > Developers > API Keys",
            ),
        ),
        optional=True,
    ),
]
