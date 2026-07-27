from memorycheck.ledger import GroundTruthLedger, ScenarioError, Scope

import pytest

ALICE = Scope(tenant_id="acme", user_id="alice")
BOB = Scope(tenant_id="acme", user_id="bob")
CAROL = Scope(tenant_id="globex", user_id="carol")


def labels(ledger, scope):
    return {(c.value, c.label) for c in ledger.snapshot(scope)}


def test_write_then_correct_supersedes():
    lg = GroundTruthLedger()
    lg.write(ALICE, "plan", "v1")
    lg.write(ALICE, "plan", "v2")
    assert labels(lg, ALICE) == {("v1", "stale"), ("v2", "current")}


def test_delete_covers_full_history():
    lg = GroundTruthLedger()
    lg.write(ALICE, "plan", "v1")
    lg.write(ALICE, "plan", "v2")
    lg.delete(ALICE, "plan")
    assert labels(lg, ALICE) == {("v1", "deleted"), ("v2", "deleted")}


def test_delete_missing_key_is_scenario_error():
    lg = GroundTruthLedger()
    with pytest.raises(ScenarioError):
        lg.delete(ALICE, "nope")


def test_ttl_expiry_only_moves_with_logical_time():
    lg = GroundTruthLedger()
    lg.write(ALICE, "promo", "code", ttl_steps=2)
    assert labels(lg, ALICE) == {("code", "current")}
    lg.advance_time(1)
    assert labels(lg, ALICE) == {("code", "current")}
    lg.advance_time(1)  # clock now 2 >= 0 + 2
    assert labels(lg, ALICE) == {("code", "expired")}


def test_rescope_moves_ownership():
    lg = GroundTruthLedger()
    lg.write(ALICE, "brief", "doc-1")
    value = lg.rescope(ALICE, BOB, "brief")
    assert value == "doc-1"
    assert ("doc-1", "deleted") in labels(lg, ALICE)
    assert ("doc-1", "current") in labels(lg, BOB)


def test_foreign_labels_by_boundary():
    lg = GroundTruthLedger()
    lg.write(ALICE, "note", "secret-1")
    assert ("secret-1", "foreign_user") in labels(lg, BOB)
    assert ("secret-1", "foreign_tenant") in labels(lg, CAROL)


def test_current_wins_precedence_for_shared_value():
    lg = GroundTruthLedger()
    lg.write(ALICE, "note", "shared-value")
    lg.write(BOB, "note", "shared-value")
    # From Bob's view the value is legitimately current, despite also
    # existing as a foreign record under Alice.
    assert lg.worst_label_for_value("shared-value", BOB) == "current"
