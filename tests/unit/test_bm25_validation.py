import pytest

from unidex.search.bm25 import Bm25Scorer
from unidex.search.inverted_index import InvertedIndex


@pytest.mark.parametrize(("k1", "b"), [(-0.1, 0.75), (1.5, -0.01), (1.5, 1.01)])
def test_invalid_parameters_are_rejected(k1: float, b: float) -> None:
    with pytest.raises(ValueError, match="must"):
        Bm25Scorer(InvertedIndex(), k1=k1, b=b)


def test_length_normalisation_can_be_switched_off() -> None:
    index = InvertedIndex()
    index.add_document(0, ["laplace"] + ["filler"] * 20)
    index.add_document(1, ["laplace", "filler"])
    hits = Bm25Scorer(index, b=0.0).search(["laplace"])
    # With b = 0 document length is ignored, so both score the same; the tie goes to id 0.
    assert [hit.doc_id for hit in hits] == [0, 1]
    assert hits[0].score == pytest.approx(hits[1].score)
