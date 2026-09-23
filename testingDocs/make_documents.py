"""
Builds the sample contracts in this directory.

    uv run python testingDocs/make_documents.py

The documents are written rather than scanned, so the text layer is real and
`parse_pdf_pages` gets clean Markdown — the same shape a client's own PDF has.
Each clause is deliberately quotable: a review should be able to point at one
sentence and say why it is a risk, which is what the evidence check needs.

Keep the generator beside the PDFs so a clause can be edited and the documents
rebuilt, rather than a binary nobody can change appearing in the repository.
"""

from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent

# Straight quotes throughout: the base-14 fonts these pages use have no smart
# quotes, and PyMuPDF writes an unrenderable glyph as "?" — which would then be
# what the model reads and quotes back.

# the page furniture: a title, generous margins, and one clause per paragraph
MARGIN = 72
WIDTH, HEIGHT = 595, 842  # A4 in points
TITLE_SIZE = 15
HEADING_SIZE = 11
BODY_SIZE = 10.5
LEADING = 15


def draw(page, blocks: list[tuple[str, str]]) -> None:
    """Writes (style, text) blocks down the page, wrapping to the margins."""
    y = MARGIN

    for style, text in blocks:
        size = {"title": TITLE_SIZE, "heading": HEADING_SIZE}.get(style, BODY_SIZE)
        font = "helv" if style == "body" else "hebo"
        box = pymupdf.Rect(MARGIN, y, WIDTH - MARGIN, HEIGHT - MARGIN)

        used = page.insert_textbox(box, text, fontname=font, fontsize=size, lineheight=1.35, align=0)
        # insert_textbox returns the space left; the height used is the rest
        y += (box.height - used) + (LEADING if style == "body" else LEADING * 0.6)


def build(name: str, pages: list[list[tuple[str, str]]]) -> Path:
    document = pymupdf.open()

    for blocks in pages:
        draw(document.new_page(width=WIDTH, height=HEIGHT), blocks)

    path = HERE / name
    document.save(str(path))
    document.close()

    return path


SERVICES_AGREEMENT = [
    [
        ("title", "MASTER SERVICES AGREEMENT"),
        (
            "body",
            'This Master Services Agreement (the "Agreement") is made on 3 February 2026 between '
            'Northwind Systems Ltd, a company registered in England (the "Supplier"), and the client '
            'identified in the Order Form (the "Client").',
        ),
        ("heading", "1. Services"),
        (
            "body",
            "1.1 The Supplier shall provide the software development and support services described in "
            "each Order Form. 1.2 The Supplier may vary the composition of the delivery team at its sole "
            "discretion and without notice to the Client.",
        ),
        ("heading", "2. Fees and payment"),
        (
            "body",
            "2.1 The Client shall pay the fees set out in the Order Form. 2.2 Invoices are payable within "
            "ninety (90) days of the invoice date. 2.3 The Supplier may increase its rates once in any "
            "twelve month period by giving fourteen (14) days notice, and the revised rates apply to work "
            "already scheduled. 2.4 Interest accrues on late payment at 8% per month.",
        ),
        ("heading", "3. Term and renewal"),
        (
            "body",
            "3.1 This Agreement begins on the Effective Date and continues for an initial term of "
            "thirty-six (36) months. 3.2 On expiry the Agreement renews automatically for successive "
            "twelve (12) month terms unless the Client gives written notice of non-renewal no less than "
            "one hundred and eighty (180) days before the end of the then-current term.",
        ),
    ],
    [
        ("heading", "4. Intellectual property"),
        (
            "body",
            "4.1 All intellectual property rights in the deliverables, including any materials provided by "
            "the Client and incorporated into them, vest in the Supplier absolutely on creation. 4.2 The "
            "Client is granted a non-exclusive, revocable licence to use the deliverables for its internal "
            "business purposes for so long as this Agreement remains in force.",
        ),
        ("heading", "5. Confidentiality"),
        (
            "body",
            "5.1 Each party shall keep the other's confidential information secret. 5.2 The Supplier may "
            "name the Client and describe the services in its marketing materials, case studies and "
            "proposals without the Client's prior approval.",
        ),
        ("heading", "6. Liability"),
        (
            "body",
            "6.1 The Client's aggregate liability under this Agreement is unlimited and is not capped by "
            "reference to the fees paid. 6.2 The Supplier's total aggregate liability for all claims "
            "arising under or in connection with this Agreement is limited to 10% of the fees paid in the "
            "three (3) months preceding the claim. 6.3 The Supplier excludes all liability for loss of "
            "data, loss of profit and business interruption however arising.",
        ),
    ],
    [
        ("heading", "7. Indemnity"),
        (
            "body",
            "7.1 The Client shall indemnify and hold harmless the Supplier, its officers and its "
            "subcontractors against all claims, losses and costs arising out of the Client's use of the "
            "deliverables, including claims that the deliverables infringe a third party's rights. 7.2 The "
            "Supplier gives no indemnity of any kind to the Client.",
        ),
        ("heading", "8. Data protection"),
        (
            "body",
            "8.1 The Supplier may transfer personal data processed under this Agreement to any country in "
            "which it or its subcontractors operate. 8.2 The Supplier shall notify the Client of a personal "
            "data breach within a reasonable period after becoming aware of it.",
        ),
        ("heading", "9. Termination"),
        (
            "body",
            "9.1 The Supplier may terminate this Agreement immediately for convenience on written notice. "
            "9.2 The Client may terminate only for material breach that remains unremedied for sixty (60) "
            "days after written notice. 9.3 On termination for any reason all fees for the remainder of the "
            "then-current term fall immediately due.",
        ),
    ],
    [
        ("heading", "10. Non-solicitation"),
        (
            "body",
            "10.1 For twenty-four (24) months after termination the Client shall not employ or engage any "
            "person who has been involved in providing the services, whether or not that person approached "
            "the Client. 10.2 Breach of clause 10.1 incurs a fee equal to twelve months of that person's "
            "salary.",
        ),
        ("heading", "11. Assignment"),
        (
            "body",
            "11.1 The Supplier may assign or novate this Agreement to any third party without the Client's "
            "consent. 11.2 The Client may not assign this Agreement without the Supplier's prior written "
            "consent, which may be withheld at the Supplier's discretion.",
        ),
        ("heading", "12. Governing law"),
        (
            "body",
            "12.1 This Agreement and any dispute arising out of it are governed by the laws of "
            "[JURISDICTION TO BE AGREED BY THE PARTIES]. 12.2 The parties submit to the exclusive "
            "jurisdiction of the courts of that jurisdiction.",
        ),
        ("heading", "13. Entire agreement"),
        (
            "body",
            "13.1 This Agreement, together with each Order Form, is the entire agreement between the "
            "parties. 13.2 In the event of conflict the terms of this Agreement prevail over any Order Form.",
        ),
    ],
]

MUTUAL_NDA = [
    [
        ("title", "MUTUAL NON-DISCLOSURE AGREEMENT"),
        (
            "body",
            "This Mutual Non-Disclosure Agreement is made on 3 February 2026 between Northwind Systems Ltd "
            '("Northwind") and the counterparty named in the signature block (the "Recipient"), '
            "in connection with discussions about a possible commercial relationship.",
        ),
        ("heading", "1. Confidential information"),
        (
            "body",
            '1.1 "Confidential Information" means all information disclosed by either party, whether or '
            "not marked as confidential, and whether disclosed orally, in writing or by any other means, "
            "including information the Recipient develops independently after the date of this Agreement.",
        ),
        ("heading", "2. Obligations"),
        (
            "body",
            "2.1 The Recipient shall not disclose Confidential Information to any third party. 2.2 The "
            "obligations in this Agreement continue in perpetuity and survive termination indefinitely. "
            "2.3 The Recipient shall, on request, certify in writing the destruction of all copies, "
            "including copies held in routine backups.",
        ),
        ("heading", "3. Remedies"),
        (
            "body",
            "3.1 The Recipient agrees that damages are an inadequate remedy and that Northwind is entitled "
            "to injunctive relief without the need to post security. 3.2 The Recipient shall pay "
            "Northwind's legal costs on an indemnity basis in any proceedings arising under this Agreement, "
            "whether or not Northwind succeeds.",
        ),
    ],
    [
        ("heading", "4. No licence"),
        (
            "body",
            "4.1 Nothing in this Agreement grants the Recipient any licence to any intellectual property. "
            "4.2 Any feedback, comments or suggestions the Recipient provides about Northwind's products "
            "become Northwind's property without payment or attribution.",
        ),
        ("heading", "5. Term"),
        (
            "body",
            "5.1 This Agreement takes effect on the date above and may be terminated by Northwind on "
            "written notice. 5.2 The Recipient may not terminate this Agreement.",
        ),
        ("heading", "6. General"),
        (
            "body",
            "6.1 This Agreement is governed by the laws of England and Wales. 6.2 Northwind may amend this "
            "Agreement on thirty (30) days written notice and continued discussions constitute acceptance "
            "of the amended terms.",
        ),
    ],
]


def main() -> None:
    for name, pages in (("services-agreement.pdf", SERVICES_AGREEMENT), ("mutual-nda.pdf", MUTUAL_NDA)):
        path = build(name, pages)
        print(f"wrote {path.relative_to(HERE.parent)} ({len(pages)} pages, {path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
