from agent_runtime.models import CaseFile, ToolEvidenceRecord


def _make_casefile():
    return CaseFile(
        created_at="2026-01-01T00:00:00Z",
        primary_target="example.com",
        primary_target_type="domain",
        depth="quick",
        model="test-model",
        instruction=None,
        hypothesis=None,
        correlate_targets=False,
    )


def _make_record(
    round_num: int = 1, tool_name: str = "osint_test"
) -> ToolEvidenceRecord:
    return ToolEvidenceRecord(
        round_num=round_num,
        phase="test",
        tool_name=tool_name,
        tool_args={},
        status="success",
        started_at="",
        duration_ms=0,
        result_preview="preview",
        raw_output="raw",
    )


def test_evidence_ids_and_recent_evidence():
    cf = _make_casefile()

    r1 = _make_record(round_num=1)
    eid1 = cf.add_evidence(r1, subagent=False)
    assert eid1 == "EV-0001"
    assert r1.evidence_id == eid1
    assert eid1 in cf.evidence

    r2 = _make_record(round_num=2)
    eid2 = cf.add_evidence(r2, subagent=False)
    assert eid2 == "EV-0002"
    assert r2.evidence_id == eid2

    # add a third and ensure recent_evidence returns last N items in order
    r3 = _make_record(round_num=3)
    eid3 = cf.add_evidence(r3, subagent=False)
    assert eid3 == "EV-0003"

    recent_two = cf.recent_evidence(2)
    assert [r.evidence_id for r in recent_two] == ["EV-0002", "EV-0003"]

