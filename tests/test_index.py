from concurrent.futures import ThreadPoolExecutor

import pytest

from secure_index.index import Index, MissingDocument, VersionConflict, chunks, fts_query


@pytest.fixture
def index(tmp_path):
    return Index(tmp_path / "index.db")


def put(index, tenant="a", doc="doc", text="alpha beta", roles=None, version=0):
    return index.upsert(tenant, doc, "Title", text, roles or [], version)


def test_chunking_overlap_and_end():
    assert chunks("one two three four five six", size=3, overlap=1) == [
        "one two three", "three four five", "five six"]
    assert chunks(" ") == []


@pytest.mark.parametrize("size,overlap", [(0, 0), (2, 2), (2, -1)])
def test_bad_chunk_config(size, overlap):
    with pytest.raises(ValueError):
        chunks("words", size, overlap)


def test_public_and_role_access(index):
    put(index, doc="pub", text="alpha public")
    put(index, doc="restricted", text="alpha restricted", roles=["staff"])
    assert {x["document_id"] for x in index.search("a", [], "alpha")} == {"pub"}
    assert {x["document_id"] for x in index.search("a", ["staff"], "alpha")} == {
        "pub", "restricted"}
    assert {x["document_id"] for x in index.search("a", ["other"], "alpha")} == {"pub"}


def test_restrictions_filter_before_limit(index):
    for i in range(12):
        put(index, doc=f"secret{i}", text="alpha " * 15, roles=["admin"])
    put(index, doc="public", text="alpha")
    assert index.search("a", [], "alpha", limit=1)[0]["document_id"] == "public"


def test_same_id_isolated_by_tenant(index):
    put(index, tenant="a", text="alpha")
    put(index, tenant="b", text="beta")
    assert index.search("a", [], "beta") == []
    assert index.search("b", [], "alpha") == []
    assert index.search("b", [], "beta")[0]["document_id"] == "doc"


def test_version_replace_and_stale_conflict(index):
    assert put(index, text="oldword")['version'] == 1
    assert put(index, text="newword", version=1, roles=["reader"])['version'] == 2
    assert index.search("a", ["reader"], "oldword") == []
    assert index.search("a", [], "newword") == []
    result = index.search("a", ["reader"], "newword")[0]
    assert result["version"] == 2
    with pytest.raises(VersionConflict):
        put(index, text="overwrite", version=1)
    assert index.search("a", ["reader"], "newword")


def test_delete_and_version_conflict(index):
    put(index)
    with pytest.raises(VersionConflict):
        index.delete("a", "doc", 2)
    assert index.search("a", [], "alpha")
    index.delete("a", "doc", 1)
    assert index.search("a", [], "alpha") == []
    with pytest.raises(MissingDocument):
        index.delete("a", "doc", 1)


def test_overlapping_updates_only_one_wins(index):
    put(index)
    def update(i):
        try:
            put(index, text=f"newword{i}", version=1)
            return True
        except VersionConflict:
            return False
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(pool.map(update, range(5))) == 1


def test_fts_query_is_not_interpreted_as_raw_syntax(index):
    put(index, text="alpha beta")
    assert index.search("a", [], 'alpha " OR (!!!')
    assert index.search("a", [], '"!@#$%^&*()') == []
    assert len(fts_query(" ".join(str(i) for i in range(30))).split(" OR ")) == 16


def test_missing_text_cannot_replace_existing(index):
    put(index)
    with pytest.raises(ValueError):
        put(index, text="   ", version=1)
    assert index.search("a", [], "alpha")


def test_matching_any_role(index):
    put(index, roles=["engineering", "admin"])
    assert index.search("a", ["sales", "engineering"], "alpha")
    assert not index.search("a", ["sales"], "alpha")
