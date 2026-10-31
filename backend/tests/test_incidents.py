"""Step 3: the incident rules, with a fake clock."""
from sqlalchemy import select

from backend import db, scheduler
from backend.config import settings
from backend.models import Incident, IncidentEvent
from backend.tests.helpers import SPOT, SUMMARY, crash, get_incident, notes, register_device

NEAR = (SPOT[0] + 0.0009, SPOT[1])      # about 100 m
FAR = (SPOT[0] + 0.1, SPOT[1])          # over 10 km


async def test_the_victim_and_the_people_nearby_are_asked(make, env):
    karim = await make("Karim", age=45, sex="male", at=SPOT)
    yacine = await make("Yacine", at=NEAR, driving=False)
    stale = await make("Stale", at=NEAR)
    far = await make("Far", at=FAR)
    await register_device("bb-1", karim)
    env.advance(0)
    # Stale: has not reported a location for an hour
    async with db.session_scope() as s:
        u = await s.get(type(stale.user), stale.id)
        u.last_seen_at = u.last_seen_at.replace(year=2020)
    await crash()
    inc = await get_incident()
    assert inc.status == "detected" and inc.confidence == "high" and inc.user_state == "pending"
    assert inc.victim_id == karim.id and inc.m3_level in ("low", "medium", "high", "extreme")
    check = (await notes(karim, "check"))[0]
    assert check.state == "open" and check.due_at is not None and [a["id"] for a in check.actions] == ["ok", "help"]
    assert len(await notes(yacine, "witness")) == 1
    assert await notes(far, "witness") == [] and await notes(stale, "witness") == []
    assert await notes(karim, "witness") == []
    assert not inc.nobody_around


async def test_each_user_only_has_their_own_notifications(make, env):
    karim = await make("Karim", at=SPOT)
    yacine = await make("Yacine", at=NEAR)
    await register_device("bb-1", karim)
    await crash()
    assert [n["kind"] for n in (await karim.get("/notifications")).json()] == ["check"]
    assert [n["kind"] for n in (await yacine.get("/notifications")).json()] == ["witness"]
    note = (await karim.get("/notifications")).json()[0]
    assert (await yacine.post(f"/notifications/{note['id']}/respond", {"action": "ok"})).status_code == 404


async def test_nobody_around_keeps_confidence_high(make, env):
    karim = await make("Karim", at=SPOT)
    await make("Far", at=FAR)
    await register_device("bb-1", karim)
    await crash()
    inc = await get_incident()
    assert inc.nobody_around and inc.confidence == "high"
    assert len(await notes(karim, "sos")) == 1
    check = (await notes(karim, "check"))[0]
    assert (await karim.post(f"/notifications/{check.id}/respond", {"action": "ok"})).status_code == 200
    inc = await get_incident()
    assert inc.user_state == "ok" and inc.confidence == "high"


async def test_ok_lowers_confidence_when_people_are_near(make, env):
    karim = await make("Karim", at=SPOT)
    await make("Yacine", at=NEAR)
    await register_device("bb-1", karim)
    await crash()
    check = (await notes(karim, "check"))[0]
    await karim.post(f"/notifications/{check.id}/respond", {"action": "ok"})
    assert (await get_incident()).confidence == "medium"


async def test_a_witness_confirming_raises_confidence_and_withdraws_the_others(make, env):
    karim = await make("Karim", at=SPOT)
    yacine = await make("Yacine", at=NEAR)
    lina = await make("Lina", at=NEAR)
    await register_device("bb-1", karim)
    await crash()
    witness = (await notes(yacine, "witness"))[0]
    assert (await yacine.post(f"/notifications/{witness.id}/respond", {"action": "confirm"})).status_code == 200
    assert (await get_incident()).confidence == "high"
    assert (await notes(yacine, "witness"))[0].state == "answered"
    other = (await notes(lina, "witness"))[0]
    assert other.state == "withdrawn"
    assert [n.title for n in await notes(lina, "caution")] == ["Accident nearby, be careful"]
    assert await notes(yacine, "caution") == []        # they answered: they already know
    assert (await lina.post(f"/notifications/{other.id}/respond", {"action": "deny"})).status_code == 409
    assert (await yacine.post(f"/notifications/{witness.id}/respond", {"action": "deny"})).status_code == 409


async def test_the_victim_answering_withdraws_the_witness_prompts(make, env):
    karim = await make("Karim", at=SPOT)
    yacine = await make("Yacine", at=NEAR)
    await register_device("bb-1", karim)
    await crash()
    check = (await notes(karim, "check"))[0]
    await karim.post(f"/notifications/{check.id}/respond", {"action": "help"})
    assert (await notes(yacine, "witness"))[0].state == "withdrawn"


async def test_silent_sos_withdraws_the_witness_prompts(make, env):
    karim = await make("Karim", at=SPOT)
    yacine = await make("Yacine", at=NEAR)
    await register_device("bb-1", karim)
    await crash()
    assert (await karim.post("/sos", {"kind": "silent"})).status_code == 200
    assert (await notes(yacine, "witness"))[0].state == "withdrawn"


async def test_a_check_in_that_times_out_leaves_the_witness_prompts_open(make, env):
    karim = await make("Karim", at=SPOT)
    yacine = await make("Yacine", at=NEAR)
    await register_device("bb-1", karim)
    await crash()
    env.advance(settings.checkin_s + 1)
    await scheduler.tick()
    assert (await notes(yacine, "witness"))[0].state == "open"


async def test_a_denial_does_not_withdraw_the_others(make, env):
    karim = await make("Karim", at=SPOT)
    yacine = await make("Yacine", at=NEAR)
    lina = await make("Lina", at=NEAR)
    await register_device("bb-1", karim)
    await crash()
    await yacine.post(f"/notifications/{(await notes(yacine, 'witness'))[0].id}/respond", {"action": "deny"})
    assert (await notes(lina, "witness"))[0].state == "open"


async def test_a_denial_with_no_confirmation_lowers_confidence(make, env):
    karim = await make("Karim", at=SPOT)
    yacine = await make("Yacine", at=NEAR)
    await register_device("bb-1", karim)
    await crash()
    witness = (await notes(yacine, "witness"))[0]
    assert (await yacine.post(f"/notifications/{witness.id}/respond", {"action": "nonsense"})).status_code == 422
    await yacine.post(f"/notifications/{witness.id}/respond", {"action": "deny"})
    assert (await get_incident()).confidence == "medium"


async def test_no_answer_in_time_means_not_responding(make, env):
    karim = await make("Karim", at=SPOT)
    await make("Yacine", at=NEAR)
    await register_device("bb-1", karim)
    await crash()
    env.advance(settings.checkin_s - 5)
    await scheduler.tick()
    assert (await get_incident()).user_state == "pending"
    env.advance(10)
    await scheduler.tick()
    inc = await get_incident()
    assert inc.user_state == "not_responding" and inc.confidence == "high"
    assert (await notes(karim, "check"))[0].state == "expired"
    late = (await notes(karim, "check"))[0]
    assert (await karim.post(f"/notifications/{late.id}/respond", {"action": "ok"})).status_code == 409


async def test_the_deadline_survives_a_restart(make, env):
    karim = await make("Karim", at=SPOT)
    await make("Yacine", at=NEAR)
    await register_device("bb-1", karim)
    await crash()
    await db.dispose()          # the process dies; the database stays
    env.advance(settings.checkin_s + 1)
    await scheduler.tick()
    assert (await get_incident()).user_state == "not_responding"


async def test_not_a_crash_is_a_false_alarm(make, env):
    karim = await make("Karim", at=SPOT)
    await register_device("bb-1", karim)
    await crash(level="none", p=0.01)
    inc = await get_incident()
    assert inc.status == "false_alarm" and await notes(karim) == []


async def test_unknown_victim_gets_no_checkin_and_a_flag(make, env):
    yacine = await make("Yacine", at=NEAR)
    await crash("bb-stranger")
    inc = await get_incident()
    assert inc.flags["unknown_victim"] and inc.flags["default_profile"] and inc.user_state is None
    assert len(await notes(yacine, "witness")) == 1


async def test_a_default_adult_is_used_without_a_profile(make, env):
    karim = await make("Karim", at=SPOT)       # no age or sex
    await register_device("bb-1", karim)
    await crash()
    inc = await get_incident()
    assert inc.flags["default_profile"]
    async with db.session() as s:
        texts = [e.text for e in (await s.scalars(select(IncidentEvent))).all()]
    assert any("default adult" in t for t in texts)


async def test_the_profile_changes_the_severity(make, env):
    old = await make("Old", at=SPOT, age=85, sex="female")
    young = await make("Young", at=FAR, age=20, sex="male")
    await register_device("bb-old", old)
    await register_device("bb-young", young)
    case = {**SUMMARY, "dv_kmh": 35.0}
    await crash("bb-old", SPOT, summary=case)
    await crash("bb-young", FAR, summary=case)
    a, b = await get_incident(1), await get_incident(2)
    assert a.m3_p > b.m3_p


async def test_two_incidents_at_once_stay_apart(make, env):
    a = await make("Aicha", at=SPOT)
    b = await make("Bilal", at=FAR)
    await register_device("bb-a", a)
    await register_device("bb-b", b)
    await crash("bb-a", SPOT)
    await crash("bb-b", FAR)
    async with db.session() as s:
        incs = (await s.scalars(select(Incident))).all()
    assert len(incs) == 2 and {i.victim_id for i in incs} == {a.id, b.id}
    assert [n.incident_id for n in await notes(a, "check")] == [1] and [n.incident_id for n in await notes(b, "check")] == [2]


async def test_reports_of_the_same_crash_merge(make, env):
    await make("Yacine", at=NEAR)
    await crash("bb-a", SPOT)
    await crash("bb-b", NEAR)           # a second car, 100 m away, 30 s later
    async with db.session() as s:
        incs = (await s.scalars(select(Incident))).all()
    assert len(incs) == 1 and incs[0].flags["reports"] == 1
    assert incs[0].confidence == "high"
    async with db.session() as s:      # the second car's own M2 answer corroborates; it doesn't start a second response
        texts = [e.text for e in (await s.scalars(select(IncidentEvent))).all()]
    assert any("bb-b confirms" in t for t in texts) and not any(t.startswith("Crash detected") and texts.count(t) > 1 for t in texts)
    env.advance(3600)                   # an hour later it is a new crash
    await crash("bb-c", SPOT, seq=9)
    async with db.session() as s:
        assert len((await s.scalars(select(Incident))).all()) == 2


async def test_a_far_report_does_not_merge(make, env):
    await crash("bb-a", SPOT)
    await crash("bb-b", FAR)
    async with db.session() as s:
        assert len((await s.scalars(select(Incident))).all()) == 2


async def test_silent_sos_means_at_least_high(make, env):
    karim = await make("Karim", at=SPOT)
    await register_device("bb-1", karim)
    await crash(summary={**SUMMARY, "dv_kmh": 5.0})
    sos = (await notes(karim, "sos"))[0]
    before = (await get_incident()).severity["shown"]
    assert (await karim.post(f"/notifications/{sos.id}/respond", {"action": "silent"})).status_code == 200
    inc = await get_incident()
    assert inc.user_state == "silent_sos" and inc.severity["shown"] in ("high", "extreme") and inc.confidence == "high"
    assert (await notes(karim, "check"))[0].state == "expired"
    assert before != "extreme" or inc.severity["shown"] == "extreme"


async def test_sos_without_a_blackbox_raises_an_incident(make, env):
    karim = await make("Karim", at=SPOT)
    yacine = await make("Yacine", at=NEAR)
    r = await karim.post("/sos", {"kind": "silent"})
    assert r.status_code == 200
    inc = await get_incident(r.json()["incident_id"])
    assert inc.flags["sos_only"] and inc.user_state == "silent_sos" and inc.severity["shown"] == "high"
    assert len(await notes(yacine, "witness")) == 1


async def test_agent_actions_wait_for_m2(make, env):
    agent = await make("Agent", "dispatcher")
    await crash(level="none")
    assert (await agent.post("/incidents/1/confirm", {})).status_code == 409
    await crash("bb-2", FAR, seq=3)
    assert (await agent.post("/incidents/2/confirm", {"severity": "bogus"})).status_code == 422
    assert (await agent.post("/incidents/2/confirm", {"severity": "extreme"})).status_code == 200
    detail = (await agent.get("/incidents/2")).json()
    assert detail["agent"] == {"confirmed": True, "severity": "extreme"} and detail["status"] == "Confirmed by Protection Civile"
    assert detail["events"][-1]["text"].startswith("You confirmed")


async def test_the_agent_sees_the_profile_only_while_the_incident_is_open(make, env):
    agent = await make("Agent", "dispatcher")
    karim = await make("Karim", at=SPOT, age=45, sex="male", blood_type="O+")
    await register_device("bb-1", karim)
    await crash()
    assert (await agent.get("/incidents/1")).json()["profile"]["blood_type"] == "O+"
    await agent.post("/incidents/1/close")
    assert (await agent.get("/incidents/1")).json()["profile"] is None


async def test_citizens_and_hospitals_cannot_read_incidents(make, env):
    citizen, hospital = await make("C"), await make("H", "hospital")
    await crash()
    for who in (citizen, hospital):
        assert (await who.get("/incidents/1")).status_code == 403
