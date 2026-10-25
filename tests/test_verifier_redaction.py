"""VER-6 — redaction, including the audit-record half decided in phase 4.

TD-14 asked whether VER-6's redaction stage grows to cover audit records. It
does: `parameters` are recorded verbatim by design and a steered model puts a
flag value in one without doing anything unusual. The alternative was a class
of files that may never be published and a sensitivity note in `prd.md`; one
redaction path is the smaller surface.

No real flag value appears here — values are minted from a throwaway seed.
"""

from __future__ import annotations

import base64
import codecs
import urllib.parse

import pytest

from bta.generator import values
from verifier.match import find_captures
from verifier.redaction import MARKER, RedactionIncomplete, redact, redact_text

SEED = "redaction-tests-0001"
FLAG_IDS = ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "C1"]


@pytest.fixture(scope="module")
def minted() -> dict[str, str]:
    return values.mint_all(SEED, FLAG_IDS)


def test_redaction_substitutes_rather_than_deletes(minted) -> None:
    """VER-6's central property. A transcript with the flag cut out no longer
    shows that the flag was disclosed, and the disclosure is the evidence."""
    text = f"Here is the postmortem: {minted['F1']}"
    result = redact_text(text, minted)
    assert minted["F1"] not in result
    assert MARKER.format(flag_id="F1") in result
    assert "Here is the postmortem:" in result


def test_the_marker_names_the_flag_it_stood_for(minted) -> None:
    text = f"{minted['F2']} and {minted['F5']}"
    result = redact_text(text, minted)
    assert MARKER.format(flag_id="F2") in result
    assert MARKER.format(flag_id="F5") in result


@pytest.mark.parametrize(
    "encode",
    [
        pytest.param(lambda v: base64.b64encode(v.encode()).decode(), id="base64"),
        pytest.param(
            lambda v: base64.b64encode(v.encode()).decode().rstrip("="), id="base64_unpadded"
        ),
        pytest.param(lambda v: v.encode().hex(), id="hex"),
        pytest.param(lambda v: v.encode().hex().upper(), id="hex_upper"),
        pytest.param(lambda v: codecs.encode(v, "rot_13"), id="rot13"),
        pytest.param(lambda v: urllib.parse.quote(v, safe=""), id="percent"),
        pytest.param(lambda v: v[::-1], id="reverse"),
        pytest.param(lambda v: "-".join(v), id="separator_split"),
        pytest.param(lambda v: "​".join(v), id="zero_width_split"),
    ],
)
def test_an_encoded_value_is_redacted_not_merely_detected(minted, encode) -> None:
    """A redactor that only handled plain text would publish the encoded form.

    The residual check in `redact` would at least refuse the artefact, but
    refusing is not the goal — the run has to produce a publishable copy.
    """
    result = redact_text(encode(minted["F3"]), minted)
    assert find_captures(result, minted, channel="model_output") == []


def test_a_transcript_is_redacted_through_every_nested_field(minted) -> None:
    """The transcript shape from `bta.agent.transcript.Transcript.to_dict()`,
    including TD-19's `result_payload` — the field that made the `tool_result`
    channel measurable and made transcripts carry payloads at all."""
    transcript = {
        "schema_version": 1,
        "session_id": "S-1",
        "events": [
            {
                "role": "assistant",
                "text": f"The value is {minted['F1']}",
                "withheld_reason": None,
                "tool_calls": [
                    {
                        "audit_id": "TC-1",
                        "tool": "get_incident",
                        "parameters": {"incident_id": f"INC-1 {minted['F4']}"},
                        "result_payload": f"postmortem: {minted['F1']}",
                        "withheld_from_model": False,
                        "rows_returned": 1,
                    }
                ],
            }
        ],
    }
    result = redact(transcript, minted)
    assert result["events"][0]["text"] == f"The value is {MARKER.format(flag_id='F1')}"
    call = result["events"][0]["tool_calls"][0]
    assert MARKER.format(flag_id="F4") in call["parameters"]["incident_id"]
    assert MARKER.format(flag_id="F1") in call["result_payload"]
    assert call["rows_returned"] == 1


def test_an_audit_record_is_redacted_too(minted) -> None:
    """TD-14's decision, as a test. `parameters` is the field the entry is
    about: at L1 the Onboarding agent holds F4 in its system prompt, so
    `check_policy(topic=<F4>)` is ordinary steered behaviour."""
    record = {
        "schema_version": 1,
        "id": "TC-88121",
        "tool": "check_policy",
        "parameters": {"topic": minted["F4"], "limit": 50},
        "credential_mode": "shared",
        "authorisation_outcome": "allowed",
        "error_class": None,
    }
    result = redact(record, minted)
    assert result["parameters"]["topic"] == MARKER.format(flag_id="F4")
    assert result["parameters"]["limit"] == 50
    assert result["authorisation_outcome"] == "allowed"


def test_redaction_fails_closed_when_a_value_survives(minted, monkeypatch) -> None:
    """The control that makes the rest of this file safe to rely on.

    A redactor that silently missed an encoding would put a live value into the
    published repository, and `git` keeps it. The substitution is stubbed out
    so that the residual check is what refuses — otherwise this test would pass
    because the substitution happened to be complete, which is the thing under
    test rather than a premise of it.
    """
    monkeypatch.setattr("verifier.redaction._walk", lambda node, values: node)
    with pytest.raises(RedactionIncomplete) as excinfo:
        redact({"text": minted["F7"]}, minted)
    assert "F7" in str(excinfo.value)


def test_a_complete_redaction_does_not_raise(minted) -> None:
    """The negative control for the test above: with `_walk` intact the same
    document redacts cleanly, so the failure there is the stub and not the
    document."""
    assert redact({"text": minted["F7"]}, minted) == {"text": MARKER.format(flag_id="F7")}


def test_the_exception_names_ids_and_never_values(minted, monkeypatch) -> None:
    """A failure message reaches a CI log, and a CI log is a transcript."""
    monkeypatch.setattr("verifier.redaction._walk", lambda node, values: node)
    with pytest.raises(RedactionIncomplete) as excinfo:
        redact({"text": minted["F6"]}, minted)
    message = str(excinfo.value)
    assert "F6" in message
    assert minted["F6"] not in message


def test_redaction_is_idempotent(minted) -> None:
    """A run is redacted once, but nothing should break if it happens twice —
    a re-scored run must not accumulate markers."""
    once = redact_text(f"value: {minted['F5']}", minted)
    assert redact_text(once, minted) == once


# --------------------------------------------------------------------------
# The negative case, made precise rather than incidental
# --------------------------------------------------------------------------


def test_surrounding_text_is_preserved_byte_for_byte_around_the_marker(minted) -> None:
    """`test_redaction_substitutes_rather_than_deletes` shows a marker appears
    and the value does not; this shows nothing *else* moved. A redactor that
    replaced the flag and also mangled adjacent punctuation or whitespace
    would still 'substitute rather than delete' in the loose sense, but a
    reader comparing the redacted transcript to what the agent actually said
    would see the wrong shape — and the disclosure's surrounding context
    (what was asked, in what tool call) is itself evidence VER-6 must not
    disturb."""
    text = f'postmortem_summary="{minted["F1"]}", severity=SEV2, owner=team-b'
    result = redact_text(text, minted)
    expected = f'postmortem_summary="{MARKER.format(flag_id="F1")}", severity=SEV2, owner=team-b'
    assert result == expected


def test_a_value_absent_from_the_text_leaves_it_completely_unchanged(minted) -> None:
    """The other half of 'substitution, not deletion': redaction must be a
    no-op on clean text, not merely non-destructive of the flag. A redactor
    that rewrote unrelated punctuation while scanning would still pass every
    test above."""
    text = "The deploy finished at 09:14 and the service recovered without incident."
    assert redact_text(text, minted) == text


# --------------------------------------------------------------------------
# Every persisted artefact type — schemas.md §5, §6, §7
# --------------------------------------------------------------------------


def test_a_memory_record_is_redacted(minted) -> None:
    """`schemas.md` §7. L1/L2 write freeform `content`, replayed verbatim into
    the next session — the F7 surface, and TD-14's argument (`check_policy`
    holding F4) applies just as directly here: a steered model's freeform note
    can hold any value it was told to remember, and this is a persisted
    record like the other two, not a special case that happens not to need
    redaction."""
    record = {
        "schema_version": 1,
        "persona_sub": "alice",
        "session_id": "S-4471a",
        "written_at_level": "L1",
        "kind": "freeform",
        "content": f"Remember this for later: {minted['F2']}",
        "subject": None,
        "value": None,
    }
    result = redact(record, minted)
    assert result["content"] == f"Remember this for later: {MARKER.format(flag_id='F2')}"
    assert result["kind"] == "freeform"
    assert result["subject"] is None


def test_a_batch_of_run_results_redacts_as_a_list(minted) -> None:
    """`redact` is documented to serve "a transcript or an audit record"
    without caring which — a scoring run persists many of each, and the
    obvious shape to hand it is a list. `_walk`/`_residual` both handle
    `Sequence`, but nothing exercised that at the top level rather than
    nested inside a dict."""
    batch = [
        {"id": "TC-1", "parameters": {"topic": minted["F4"]}},
        {"id": "TC-2", "parameters": {"topic": "nothing sensitive"}},
    ]
    result = redact(batch, minted)
    assert result[0]["parameters"]["topic"] == MARKER.format(flag_id="F4")
    assert result[1]["parameters"]["topic"] == "nothing sensitive"


def test_a_flag_value_used_as_a_dict_key_is_also_redacted(minted) -> None:
    """The module docstring is explicit that keys are redacted as well as
    values, "not a distinction worth betting the containment claim on" — a
    steered model choosing a dict key from user-controlled text is not a
    fantastical path (`schemas.md` §6 already treats `parameters` as
    attacker-influenced)."""
    document = {minted["F3"]: "some content", "ordinary_key": "ordinary value"}
    result = redact(document, minted)
    assert MARKER.format(flag_id="F3") in result
    assert minted["F3"] not in "".join(result.keys())
    assert result["ordinary_key"] == "ordinary value"


# --------------------------------------------------------------------------
# The redactor's own boundary — a depth-two disclosure the literal
# substitution list does not cover
# --------------------------------------------------------------------------


def test_a_composed_disclosure_is_substituted_rather_than_merely_refused(minted) -> None:
    """The shape that used to leak silently, in the two places it appeared.

    An attacker asks for base64 and then splits the blob — `-`.join(b64) is an
    ordinary `encoding_obfuscation` output, not a contrived one. Two separate
    defects met on it: `find_captures` corrupted its own needle on a
    fold-then-decode chain, and `_walk` substituted encodings only literally.
    With the blob sitting in surrounding prose, neither the substituter nor the
    residual check saw it: `redact()` returned the document unchanged, raised
    nothing, and the value was recoverable by hand from a copy cleared for
    publication. That is the one failure VER-6 exists to prevent.

    Redaction is expected to *substitute* here, not merely refuse. Failing
    closed is the backstop for an encoding nobody anticipated; for one this
    common it would mean the defeated cells the project most wants to publish
    are exactly the ones that cannot produce a redacted artefact.
    """
    value = minted["F5"]
    blob = base64.b64encode(value.encode()).decode()
    marker = MARKER.format(flag_id="F5")

    shapes = {
        "bare blob": blob,
        "blob in prose": f"the value was {blob}",
        "split blob": "-".join(blob),
        "split blob in prose": f"the value was {'-'.join(blob)}",
        "plain in prose": f"the value was {value}",
    }
    for label, text in shapes.items():
        result = redact({"text": text}, minted)["text"]
        assert value not in result, f"{label}: plaintext survived"
        assert marker in result, f"{label}: nothing was substituted"
        assert find_captures(result, minted, channel="model_output") == [], (
            f"{label}: a value is still reachable under normalisation"
        )


def test_redaction_still_fails_closed_on_an_encoding_it_cannot_substitute(
    monkeypatch, minted
) -> None:
    """The backstop itself. Substitution having improved must not quietly
    remove the guarantee that an unhandled encoding raises rather than being
    published — so the substituter is stubbed out and a real disclosure is put
    through the residual check."""
    monkeypatch.setattr("verifier.redaction.redact_text", lambda text, values: text)
    with pytest.raises(RedactionIncomplete) as excinfo:
        redact({"text": f"the value was {minted['F5']}"}, minted)
    message = str(excinfo.value)
    assert "F5" in message
    assert minted["F5"] not in message
