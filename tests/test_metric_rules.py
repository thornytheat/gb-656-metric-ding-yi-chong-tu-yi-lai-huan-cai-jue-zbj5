from app.modules.m3_compat.metric_rules import canonical, resolve, signature


def test_spaces_do_not_change_signature():
    assert signature("pay + refund") == signature("pay+refund")


def test_commutative_terms_have_one_signature():
    assert signature("refund+pay") == signature("pay+refund")


def test_aliases_resolve_before_signature():
    assert signature("paid_amount+refund", {"paid_amount": "pay"}) == signature("pay+refund")


def test_canonical_is_stable():
    value = canonical(" refund + pay ")
    assert canonical(value) == value


def test_same_definition_chooses_priority_source():
    rows = [{"name": "gmv", "version": "v1", "expression": "pay+refund", "priority": 1, "source": "sales"}, {"name": "gmv", "version": "v1", "expression": "refund+pay", "priority": 2, "source": "finance"}]
    assert resolve(rows)["chosen"][0]["source"] == "finance"


def test_conflicting_definition_is_not_silently_overwritten():
    rows = [{"name": "gmv", "version": "v1", "expression": "pay+refund"}, {"name": "gmv", "version": "v1", "expression": "pay-refund"}]
    assert resolve(rows)["conflicts"][0]["reason"] == "DEFINITION_CONFLICT"


def test_rule_order_does_not_change_result():
    rows = [{"name": "gmv", "version": "v1", "expression": "pay+refund", "source": "b"}, {"name": "gmv", "version": "v1", "expression": "refund+pay", "source": "a"}]
    assert resolve(rows) == resolve(list(reversed(rows)))


def test_empty_definition_is_quarantined():
    assert resolve([{"name": "gmv", "version": "v1", "expression": ""}])["conflicts"][0]["reason"] == "DEFINITION_EMPTY"
