"""Did the episode succeed, and how do we know? — the verdict ladder.

abacus records lifecycle and never outcome, so this module supplies the denominator
of the priority-1 measure. Five ways of learning an outcome exist and they are not
equally trustworthy, so each answer carries the *basis* it was reached by:

    declared > structured > lexical > inferential > unstated

Three properties are the reason the module is shaped this way.

**`unstated` is the dominant real case and is not a failure.** Ten of twelve closed
issues measured on this machine carry `bd`'s default `close_reason` of exactly
`"Closed"`. An episode nobody assessed is evidence of nothing; counted as a
rejection it would report a working harness as a failing one, and counted as a
success it would put unexamined work into the passing denominator. It is excluded
from both and published as a coverage shortfall instead — which is why
`verdict_coverage` is a headline number and gates tokens per outcome
(`adr/008-verdict-coverage-gates-tokens-per-outcome.md`).

**The lexicon refuses rather than guesses.** Free text is read computationally, and
where the text points both ways the answer is `unstated` with `reason: "ambiguous"`.
A coin flip here is worse than a gap: an `accepted` inflates the denominator and a
`rejected` deflates it, and there is no evidence for either. Negation is handled
explicitly because `"not working"` contains `"working"` — the single most damaging
error available to this module, since it moves an episode into the passing count.

**`inferential` ranks below `lexical`, not above it**
(`adr/013-the-inferential-verdict-is-a-backfill-tool.md`). A stored inferential
verdict is a backfill for issues closed before the `accepted:` habit; where a
computational read is available it wins, otherwise switching inference on would
quietly weaken the basis of episodes that already had a better one.

Pure: no I/O, no subprocess, no clock. That is a testability requirement rather than
tidiness — it lets the measure layer above be driven from tables of records with no
filesystem at all.
"""

import re


LEXICON_VERSION = "lexical-v1"

# The rungs in precedence order. A report's vocabulary binds to this, so an
# undeclared basis would reach a skill with no instruction covering it.
LADDER = ("declared", "structured", "lexical", "inferential", "unstated")

BASES = LADDER

VERDICTS = (
    "accepted",
    "rejected",
    "abandoned",
    "superseded",
    "partial",
    "unstated",
)

# What a human may write into `hfit_verdict`. `partial` is absent deliberately: it
# is a statement about the *capture* and is derived from `abacus_partial`, so
# allowing it to be declared would let a hand-set key claim an interrupted session
# that abacus recorded as clean — the one direction of that flag nothing can check.
# `unstated` is absent because declaring it says nothing a missing key does not.
DECLARABLE = ("accepted", "rejected", "abandoned", "superseded")

# Why a verdict is `unstated`, so a report can say which kind of silence this was.
UNSTATED_REASONS = ("no_close_reason", "bd_default", "no_signal", "ambiguous")

# `bd`'s own default when `--reason` is omitted. Measured, and the reason the
# lexicon must not treat "closed" as a success word.
_BD_DEFAULT = "closed"

_STRUCTURED = re.compile(
    r"^\s*(accepted|rejected|abandoned|superseded)\s*:", re.IGNORECASE
)

# Contractions are expanded from an explicit map rather than by a clever rule,
# because the clever rule ("strip n't") turns "won't" into "wo not" and silently
# stops matching. A short honest table beats a general wrong one.
_CONTRACTIONS = (
    ("won't", "will not"),
    ("can't", "can not"),
    ("cannot", "can not"),
    ("doesn't", "does not"),
    ("don't", "do not"),
    ("didn't", "did not"),
    ("isn't", "is not"),
    ("wasn't", "was not"),
    ("aren't", "are not"),
    ("weren't", "were not"),
    ("hasn't", "has not"),
    ("haven't", "have not"),
    ("couldn't", "could not"),
    ("wouldn't", "would not"),
    ("shouldn't", "should not"),
)

_NEGATIONS = frozenset(("not", "never", "no", "nor", "without", "failed"))

# How far back a negation reaches. Two tokens covers "not yet working" and
# "does not really work" without reaching across a clause boundary into an
# unrelated sentence.
_NEGATION_WINDOW = 2

_LEXICON = {
    "accepted": frozenset(
        (
            "work",
            "works",
            "worked",
            "working",
            "fixed",
            "fixes",
            "done",
            "complete",
            "completed",
            "resolved",
            "resolves",
            "shipped",
            "merged",
            "passing",
            "passes",
            "green",
            "verified",
            "success",
            "succeeded",
            "accepted",
            "implemented",
            "landed",
        )
    ),
    "rejected": frozenset(
        (
            "broken",
            "breaks",
            "failing",
            "fails",
            "failure",
            "wrong",
            "regression",
            "revert",
            "reverted",
            "rejected",
            "incorrect",
            "unusable",
        )
    ),
    "abandoned": frozenset(
        (
            "abandoned",
            "wontfix",
            "obsolete",
            "dropped",
            "stale",
            "unnecessary",
            "deprioritised",
            "deprioritized",
        )
    ),
    "superseded": frozenset(
        (
            "superseded",
            "supersedes",
            "duplicate",
            "dupe",
            "replaced",
        )
    ),
}

_TOKEN = re.compile(r"[a-z0-9]+")


def _metadata(issue):
    meta = issue.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _tokens(text):
    lowered = text.lower()
    for contraction, expansion in _CONTRACTIONS:
        lowered = lowered.replace(contraction, expansion)
    return _TOKEN.findall(lowered)


def _negated(tokens, index):
    """Whether a negation sits within the window before `index`."""
    start = max(0, index - _NEGATION_WINDOW)
    return any(token in _NEGATIONS for token in tokens[start:index])


def _lexical(text):
    """Return `(verdict, reason)` — exactly one of them set.

    A hit whose negation was suppressed contributes nothing rather than flipping to
    the opposite category: "not broken" is weak evidence of success at best, and
    inventing an `accepted` from it is the error this whole function exists to avoid.
    """
    tokens = _tokens(text)
    hits = set()
    for index, token in enumerate(tokens):
        for category, words in _LEXICON.items():
            if token in words and not _negated(tokens, index):
                hits.add(category)

    if not hits:
        return None, "no_signal"
    if len(hits) > 1:
        return None, "ambiguous"
    return hits.pop(), None


def _rungs(issue):
    """Walk the ladder and return `(verdict, basis, reason)`."""
    meta = _metadata(issue)
    declared = meta.get("hfit_verdict")
    basis_hint = meta.get("hfit_verdict_basis")

    # `declared` outranks everything a machine can read off the text. An
    # unrecognised value falls through instead: a weaker but *known* basis beats an
    # unhandled string in the headline.
    if declared in DECLARABLE and basis_hint != "inferential":
        return declared, "declared", None

    reason = issue.get("close_reason")
    text = reason.strip() if isinstance(reason, str) else ""

    if text:
        match = _STRUCTURED.match(text)
        if match:
            return match.group(1).lower(), "structured", None

    # The lexical read is attempted before any stored inferential verdict, so
    # switching inference on can never downgrade an episode's basis (ADR-013).
    lexical_reason = "no_close_reason"
    if text:
        if text.lower() == _BD_DEFAULT:
            lexical_reason = "bd_default"
        else:
            found, lexical_reason = _lexical(text)
            if found is not None:
                return found, "lexical", None

    if declared in DECLARABLE and basis_hint == "inferential":
        return declared, "inferential", None

    return "unstated", "unstated", lexical_reason


def classify(issue):
    """Classify one closed beads issue. Reads `issue`; never writes to it.

    `capture_ok` reports whether an abacus capture exists for the episode at all. A
    `closed_at` with no `abacus_schema` means the work happened unattributed: it
    still counts in the autonomy denominators, and is excluded from tokens per
    outcome because there is no token figure to include.
    """
    meta = _metadata(issue)
    verdict_value, basis, reason = _rungs(issue)

    # `is True` rather than truthiness: `bd` preserves JSON types so the real flag is
    # a bool, but a hand-set `"false"` is a truthy string that would otherwise
    # downgrade every verdict on the issue.
    partial = meta.get("abacus_partial") is True

    # Only a success is downgraded. A rejection is a rejection whether or not the
    # capture was clean, and laundering it into `partial` would hide a failure.
    if partial and verdict_value == "accepted":
        verdict_value = "partial"

    return {
        "issue_id": issue.get("id"),
        "verdict": verdict_value,
        "basis": basis,
        "capture_ok": "abacus_schema" in meta,
        "partial": partial,
        "reason": reason,
        "lexicon_version": LEXICON_VERSION if basis == "lexical" else None,
    }


def coverage(verdicts):
    """The share of episodes whose outcome is stated, plus the basis mix.

    Over an empty population the answer is `None`, never `0.0`: zero would be a claim
    about the user's work, and would send someone looking for a habit problem they do
    not have. Unknown is never zero.

    The mix is published beside the number because the headline cannot show it —
    60% built entirely from the lexicon is a different fact from 60% built from
    `accepted:` prefixes, and only the second is worth trusting a delta on.
    """
    by_basis = dict((basis, 0) for basis in BASES)
    stated = 0
    total = 0

    for record in verdicts:
        basis = record.get("basis")
        total += 1
        if basis in by_basis:
            by_basis[basis] += 1
        if basis in ("declared", "structured", "lexical", "inferential"):
            stated += 1

    if total == 0:
        return {
            "coverage": None,
            "n": 0,
            "stated": 0,
            "by_basis": by_basis,
            "reason": "no_episodes",
        }

    return {
        "coverage": stated / float(total),
        "n": total,
        "stated": stated,
        "by_basis": by_basis,
        "reason": None,
    }
