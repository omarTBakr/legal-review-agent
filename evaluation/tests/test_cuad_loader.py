"""Reading CUAD's JSON, and the curated risk-category map that sits beside it."""

import json

from evaluation.cuad.download import MEMBER, sha256_of
from evaluation.cuad.loader import CATEGORIES_FILE, load_risk_categories, parse_contracts

LIABILITY_QUESTION = 'Highlight the parts related to "Uncapped Liability". Details: Is there a clause with no cap?'
COMPETE_QUESTION = 'Highlight the parts related to "Non-Compete". Details: Is there a non-compete?'

PAYLOAD = {
    "version": "aok_v1.0",
    "data": [
        {
            "title": "ACME-SUPPLY-AGREEMENT",
            "paragraphs": [
                {
                    "context": "1. Liability. The Buyer's liability is unlimited.",
                    "qas": [
                        {
                            "id": "ACME-SUPPLY-AGREEMENT__Uncapped Liability",
                            "question": LIABILITY_QUESTION,
                            "answers": [{"text": "The Buyer's liability is unlimited.", "answer_start": 15}],
                            "is_impossible": False,
                        },
                        {
                            "id": "ACME-SUPPLY-AGREEMENT__Non-Compete",
                            "question": COMPETE_QUESTION,
                            "answers": [],
                            "is_impossible": True,
                        },
                    ],
                }
            ],
        }
    ],
}


def test_the_category_comes_from_the_question_id():
    contract = parse_contracts(PAYLOAD)[0]

    assert [clause.category for clause in contract.clauses] == ["Uncapped Liability", "Non-Compete"]


def test_the_definition_is_the_text_after_details():
    """The boilerplate before it says nothing an extraction prompt can use."""
    clause = parse_contracts(PAYLOAD)[0].clause("Uncapped Liability")

    assert clause.definition == "Is there a clause with no cap?"


def test_a_question_with_no_details_keeps_the_whole_question():
    payload = json.loads(json.dumps(PAYLOAD))
    payload["data"][0]["paragraphs"][0]["qas"][0]["question"] = "What is the cap?"

    assert parse_contracts(payload)[0].clause("Uncapped Liability").definition == "What is the cap?"


def test_an_unanswered_category_is_a_real_negative_not_missing_data():
    """This is what makes an abstention scoreable."""
    clause = parse_contracts(PAYLOAD)[0].clause("Non-Compete")

    assert clause.spans == ()
    assert clause.annotated is False


def test_the_contracts_text_is_carried_through():
    assert "liability is unlimited" in parse_contracts(PAYLOAD)[0].text


def test_annotated_clauses_can_be_narrowed_to_some_categories():
    contract = parse_contracts(PAYLOAD)[0]

    assert [clause.category for clause in contract.annotated_clauses({"Uncapped Liability"})] == ["Uncapped Liability"]
    assert contract.annotated_clauses({"Non-Compete"}) == []


def test_blank_and_whitespace_answers_are_dropped():
    payload = json.loads(json.dumps(PAYLOAD))
    payload["data"][0]["paragraphs"][0]["qas"][0]["answers"] = [{"text": "  "}, {"text": "real span"}]

    assert parse_contracts(payload)[0].clause("Uncapped Liability").spans == ("real span",)


def test_the_curated_map_covers_all_41_cuad_categories():
    """A category the map forgets would silently drop out of the risk denominator."""
    assert len(load_risk_categories().all_categories) == 41


def test_the_obvious_risks_are_marked_risky():
    categories = load_risk_categories()

    for category in ("Uncapped Liability", "Cap On Liability", "Ip Ownership Assignment", "Non-Compete", "Liquidated Damages"):
        assert category in categories.risky, category


def test_metadata_categories_are_not_risks():
    categories = load_risk_categories()

    for category in ("Document Name", "Parties", "Agreement Date", "Governing Law"):
        assert category not in categories.risky, category


def test_a_carve_out_that_reduces_a_restriction_is_not_a_risk():
    """Flagging an exception to a non-compete as a risk would be wrong, not merely noisy."""
    assert "Competitive Restriction Exception" not in load_risk_categories().risky


def test_every_high_stakes_category_is_also_a_risk():
    categories = load_risk_categories()

    assert categories.high_stakes <= categories.risky


def test_liability_ip_and_termination_are_the_high_stakes_ones():
    categories = load_risk_categories()

    for category in ("Uncapped Liability", "Cap On Liability", "Ip Ownership Assignment", "Termination For Convenience"):
        assert categories.is_high_stakes(category), category


def test_every_category_carries_a_note_explaining_the_judgement():
    """The map is a judgement call, so it has to be reviewable without reading the code."""
    for category, note in load_risk_categories().notes.items():
        assert note.strip(), category


def test_the_map_says_why_indemnities_are_absent():
    """CUAD v1 has no indemnity category; the file must not pretend otherwise."""
    about = " ".join(json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))["about"])

    assert "indemnit" in about.lower()


def test_the_checksum_helper_hashes_a_file(tmp_path):
    path = tmp_path / MEMBER
    path.write_bytes(b"")

    assert sha256_of(path) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
