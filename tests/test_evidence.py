from enums.RiskSeverity import RiskSeverity
from schemas.key_risk import KeyRisk
from utils.batching import split_pages_into_batches
from utils.evidence import carry_verification, find_quote, normalize, verify_risks

PAGES = [
    "This Agreement is made between Acme Ltd and Beta LLC.",
    "The Supplier shall indemnify the Customer against all losses.",
    "**9. Liability.** The Supplier's liability under this Agreement is unlimited.",
    "Either party may terminate on 30 days' written notice.",
    "This Agreement is governed by the laws of England.",
]

BATCH = split_pages_into_batches(PAGES, 5)[0].markdown


def risk(quote: str, page: int | None = None, verified: bool = False) -> KeyRisk:
    return KeyRisk(description="d", severity=RiskSeverity.HIGH, quote=quote, page=page, quote_verified=verified)


# --- normalize -----------------------------------------------------------


def test_normalize_ignores_case_whitespace_and_markdown():
    assert normalize("**9. Liability.**\n  The  SUPPLIER") == "9. liability. the supplier"


def test_normalize_straightens_curly_quotes_and_dashes():
    assert normalize("the Supplier’s “fees” — all") == 'the supplier\'s "fees" - all'


def test_normalize_drops_page_markers():
    assert "page" not in normalize("<!-- page 3 --> text")


# --- find_quote ----------------------------------------------------------


def test_an_exact_quote_is_found_on_its_page():
    assert find_quote("The Supplier shall indemnify the Customer against all losses.", BATCH) == 2


def test_a_quote_on_page_three_of_five_reports_three():
    assert find_quote("liability under this Agreement is unlimited", BATCH) == 3


def test_markdown_and_whitespace_differences_do_not_matter():
    assert find_quote("9. Liability. The Supplier's liability\nunder this Agreement", BATCH) == 3


def test_curly_quotes_in_the_quote_still_match():
    assert find_quote("The Supplier’s liability under this Agreement", BATCH) == 3


def test_a_small_slip_still_matches():
    """A dropped letter in copying is not an invented quote."""
    assert find_quote("Either party may terminate on 30 days writen notice.", BATCH) == 4


def test_an_invented_quote_is_not_found():
    assert find_quote("The Customer shall pay a penalty of ten million pounds.", BATCH) is None


def test_a_quote_stitched_from_two_pages_is_not_found():
    assert find_quote("liability is unlimited. Either party may terminate at will", BATCH) is None


def test_a_short_quote_proves_nothing():
    assert find_quote("the Supplier", BATCH) is None


def test_an_empty_quote_is_not_found():
    assert find_quote("", BATCH) is None


# --- verify_risks --------------------------------------------------------


def test_a_found_quote_is_verified_and_takes_the_real_page():
    """The model's page is replaced by where the quote actually is."""
    [checked] = verify_risks([risk("governed by the laws of England", page=1)], BATCH)

    assert checked.quote_verified
    assert checked.page == 5


def test_a_missing_quote_keeps_the_risk_but_flags_it():
    [checked] = verify_risks([risk("governed by the laws of France", page=5)], BATCH)

    assert not checked.quote_verified
    assert checked.page == 5


def test_verify_risks_does_not_change_its_input():
    original = risk("governed by the laws of England")

    verify_risks([original], BATCH)

    assert not original.quote_verified


# --- carry_verification ---------------------------------------------------


def test_an_unchanged_quote_stays_verified():
    known = [risk("governed by the laws of England", page=5, verified=True)]

    [carried] = carry_verification([risk("governed by the laws of England")], known)

    assert carried.quote_verified and carried.page == 5


def test_part_of_a_verified_quote_stays_verified():
    known = [risk("This Agreement is governed by the laws of England.", page=5, verified=True)]

    [carried] = carry_verification([risk("governed by the laws of England")], known)

    assert carried.quote_verified


def test_a_rewritten_quote_loses_its_verification():
    known = [risk("governed by the laws of England", page=5, verified=True)]

    [carried] = carry_verification([risk("English law governs the whole agreement")], known)

    assert not carried.quote_verified


def test_a_quote_that_was_never_verified_does_not_become_verified():
    known = [risk("governed by the laws of France", page=5, verified=False)]

    [carried] = carry_verification([risk("governed by the laws of France")], known)

    assert not carried.quote_verified
