import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration
API = "/api/v1"


@pytest.fixture
def world(make_user):
    """Two institutions with their own admins and researchers, plus an independent researcher."""
    root = make_user("root@platform.org", "Platform Admin")
    lisbon_admin = make_user("admin@lisbon.pt", "Lisbon Admin")
    lisbon_res = make_user("res@lisbon.pt", "Lisbon Researcher")
    paris_admin = make_user("admin@paris.fr", "Paris Admin")
    indep = make_user("indep@gmail.com", "Independent")
    lis = root.post(f"{API}/institutions", json={"name": "Arquivo Nacional", "slug": "arquivo-nacional", "country": "PT"}).json()
    par = root.post(f"{API}/institutions", json={"name": "Bibliothèque", "slug": "bibliotheque", "country": "FR"}).json()
    root.put(f"{API}/institutions/{lis['id']}/members", json={"email": lisbon_admin.email, "role": "institution_admin"})
    root.put(f"{API}/institutions/{par['id']}/members", json={"email": paris_admin.email, "role": "institution_admin"})
    r = lisbon_admin.put(f"{API}/institutions/{lis['id']}/members", json={"email": lisbon_res.email, "role": "member"})
    assert r.status_code == 200
    return dict(root=root, lisbon_admin=lisbon_admin, lisbon_res=lisbon_res, paris_admin=paris_admin, indep=indep,
                lis=lis, par=par)


def test_only_platform_admin_creates_institutions(world):
    r = world["lisbon_admin"].post(f"{API}/institutions", json={"name": "X", "slug": "x-inst"})
    assert r.status_code == 403
    r = world["root"].post(f"{API}/institutions", json={"name": "Dup", "slug": "arquivo-nacional"})
    assert r.status_code == 409


def test_institution_admin_cannot_manage_other_institution(world):
    r = world["paris_admin"].put(f"{API}/institutions/{world['lis']['id']}/members",
                                 json={"email": world["indep"].email, "role": "member"})
    assert r.status_code == 404  # existence of other institutions is not revealed


def test_last_institution_admin_cannot_be_removed(world):
    uid = world["lisbon_admin"].user["id"]
    r = world["lisbon_admin"].delete(f"{API}/institutions/{world['lis']['id']}/members/{uid}")
    assert r.status_code == 400 and r.json()["error"]["code"] == "last_admin"


def test_independent_researcher_project_lifecycle(world):
    indep = world["indep"]
    r = indep.post(f"{API}/projects", json={"title": "Cartas de Évora", "languages": ["por"], "period_start": 1600, "period_end": 1699})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["slug"] == "cartas-de-evora" and p["institution_id"] is None and "delete" in p["permissions"]
    # second project with same title gets unique slug
    p2 = indep.post(f"{API}/projects", json={"title": "Cartas de Évora"}).json()
    assert p2["slug"] == "cartas-de-evora-2"
    r = indep.patch(f"{API}/projects/{p['id']}", json={"description": "Correspondência", "is_archived": True})
    assert r.status_code == 200 and r.json()["is_archived"] is True
    assert indep.get(f"{API}/projects").json()["total"] == 1  # archived hidden by default
    assert indep.get(f"{API}/projects", params={"include_archived": True}).json()["total"] == 2
    assert indep.delete(f"{API}/projects/{p2['id']}").status_code == 204


@pytest.mark.parametrize("payload,code", [
    ({"title": "x", "languages": ["portuguese"]}, 422),
    ({"title": "Valid", "period_start": 1800, "period_end": 1700}, 422),
    ({"title": "Valid", "languages": ["PT"]}, 422),
])
def test_project_validation(world, payload, code):
    assert world["indep"].post(f"{API}/projects", json=payload).status_code == code


def test_cannot_create_project_in_foreign_institution(world):
    r = world["indep"].post(f"{API}/projects", json={"title": "Intrusion", "institution_id": world["lis"]["id"]})
    assert r.status_code == 403


def test_institution_isolation(world):
    lis_proj = world["lisbon_res"].post(f"{API}/projects", json={"title": "Inquisição de Lisboa",
                                                                  "institution_id": world["lis"]["id"]}).json()
    # Other institution and independent researcher cannot see or guess it.
    for who in ("paris_admin", "indep"):
        assert world[who].get(f"{API}/projects/{lis_proj['id']}").status_code == 404
        assert world[who].get(f"{API}/projects").json()["total"] == 0
        assert world[who].get(f"{API}/projects/{lis_proj['id']}/collections").status_code == 404
    # Institution admin sees every project of the institution; platform admin sees all.
    assert world["lisbon_admin"].get(f"{API}/projects/{lis_proj['id']}").status_code == 200
    assert world["root"].get(f"{API}/projects").json()["total"] == 1


def test_project_roles_enforced(world, make_user):
    pi = world["lisbon_res"]
    p = pi.post(f"{API}/projects", json={"title": "Registos Paroquiais", "institution_id": world["lis"]["id"]}).json()
    reader = make_user("reader@lisbon.pt")
    editor = make_user("editor@lisbon.pt")
    # Non-members of the institution cannot join an institutional project.
    r = pi.put(f"{API}/projects/{p['id']}/members", json={"email": reader.email, "role": "read_only"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_institution_member"
    for u in (reader, editor):
        world["lisbon_admin"].put(f"{API}/institutions/{world['lis']['id']}/members", json={"email": u.email, "role": "member"})
    assert pi.put(f"{API}/projects/{p['id']}/members", json={"email": reader.email, "role": "read_only"}).status_code == 200
    assert pi.put(f"{API}/projects/{p['id']}/members", json={"email": editor.email, "role": "transcription_editor"}).status_code == 200

    assert reader.get(f"{API}/projects/{p['id']}").json()["permissions"] == ["view"]
    assert reader.post(f"{API}/projects/{p['id']}/collections", json={"title": "Livro 1"}).status_code == 403
    assert reader.patch(f"{API}/projects/{p['id']}", json={"title": "Hijack"}).status_code == 403
    assert editor.put(f"{API}/projects/{p['id']}/members", json={"email": reader.email, "role": "researcher"}).status_code == 403
    assert set(editor.get(f"{API}/projects/{p['id']}").json()["permissions"]) == {"view", "transcribe", "export"}

    members = pi.get(f"{API}/projects/{p['id']}/members").json()
    roles = {m["email"]: m["role"] for m in members}
    assert roles == {pi.email: "owner", reader.email: "read_only", editor.email: "transcription_editor"}
    owner_id = pi.user["id"]
    assert pi.delete(f"{API}/projects/{p['id']}/members/{owner_id}").status_code == 400
    assert pi.delete(f"{API}/projects/{p['id']}/members/{reader.user['id']}").status_code == 204
    assert reader.get(f"{API}/projects/{p['id']}").status_code == 404


def test_collections_crud_with_dublin_core(world):
    indep = world["indep"]
    p = indep.post(f"{API}/projects", json={"title": "Notariais"}).json()
    base = f"{API}/projects/{p['id']}/collections"
    bad = indep.post(base, json={"title": "C", "dc_metadata": {"author": "X"}})
    assert bad.status_code == 422
    c = indep.post(base, json={"title": "Cartório de Braga", "archival_reference": "PT/ADB/NOT/CNBRG01",
                               "holding_institution": "Arquivo Distrital de Braga",
                               "dc_metadata": {"language": ["por", "lat"], "coverage": "Braga", "date": "1720/1755"}})
    assert c.status_code == 201, c.text
    cid = c.json()["id"]
    assert indep.patch(f"{base}/{cid}", json={"description": "Livros de notas"}).json()["description"] == "Livros de notas"
    assert indep.get(base).json()[0]["dc_metadata"]["coverage"] == "Braga"
    # Project with collections cannot be deleted (must archive or empty first).
    r = indep.delete(f"{API}/projects/{p['id']}")
    assert r.status_code == 409
    assert indep.delete(f"{base}/{cid}").status_code == 204
    assert indep.delete(f"{API}/projects/{p['id']}").status_code == 204


def test_collection_from_other_project_is_not_reachable(world):
    indep = world["indep"]
    p1 = indep.post(f"{API}/projects", json={"title": "P1"}).json()
    p2 = indep.post(f"{API}/projects", json={"title": "P2"}).json()
    c = indep.post(f"{API}/projects/{p1['id']}/collections", json={"title": "C1"}).json()
    assert indep.get(f"{API}/projects/{p2['id']}/collections/{c['id']}").status_code == 404


def test_audit_trail_records_actions_and_is_append_only(world, db):
    indep = world["indep"]
    p = indep.post(f"{API}/projects", json={"title": "Audited"}).json()
    indep.post(f"{API}/projects/{p['id']}/collections", json={"title": "C"})
    logs = indep.get(f"{API}/audit-logs", params={"project_id": p["id"]}).json()
    actions = [i["action"] for i in logs["items"]]
    assert actions == ["collection.create", "project.create"]
    assert all(i["actor_email"] == indep.email for i in logs["items"])
    # Paris admin sees none of it.
    assert world["paris_admin"].get(f"{API}/audit-logs", params={"project_id": p["id"]}).json()["total"] == 0
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE audit_logs SET action = 'tampered'"))
    db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM audit_logs"))
    db.rollback()


def test_audit_survives_project_deletion(world, db):
    indep = world["indep"]
    p = indep.post(f"{API}/projects", json={"title": "Ephemeral"}).json()
    assert indep.delete(f"{API}/projects/{p['id']}").status_code == 204
    n = db.scalar(text("SELECT count(*) FROM audit_logs WHERE project_id = :p OR entity_id = :e"),
                  {"p": p["id"], "e": p["id"]})
    assert n == 2  # create + delete


def test_dashboard_counts_are_real(world, db):
    indep = world["indep"]
    empty = indep.get(f"{API}/dashboard").json()
    assert (empty["projects"], empty["collections"]) == (0, 0)
    assert empty["documents"] == 0 and empty["pages"] == 0 and empty["unavailable"] == {}
    assert empty["quality"] == {"ground_truth_pages": 0, "evaluation_results": 0, "methodology_version": "scriptorium-metrics/1", "by_model": []}
    assert empty["line_status"] == {"unreviewed": 0, "corrected": 0, "reviewed": 0}
    p = indep.post(f"{API}/projects", json={"title": "Counted"}).json()
    for i in range(3):
        indep.post(f"{API}/projects/{p['id']}/collections", json={"title": f"C{i}"})
    d = indep.get(f"{API}/dashboard").json()
    assert (d["projects"], d["collections"], d["researchers"]) == (1, 3, 1)
    db_counts = db.execute(text("SELECT (SELECT count(*) FROM projects), (SELECT count(*) FROM collections)")).one()
    assert tuple(db_counts) == (1, 3)
    assert world["root"].get(f"{API}/dashboard").json()["institutions"] == 2
    assert world["paris_admin"].get(f"{API}/dashboard").json()["projects"] == 0
