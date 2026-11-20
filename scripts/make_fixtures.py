"""Generate deterministic public demo fixtures, using the checked-in synthetic text."""
from pathlib import Path
from docx import Document
from docx.shared import Pt
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer

ROOT = Path(__file__).resolve().parent.parent


def main():
    source = ROOT / "fixtures" / "Acme-Service-Agreement.txt"
    blocks = source.read_text(encoding="utf-8").strip().split("\n\n")
    doc = Document()
    doc.styles["Normal"].font.size = Pt(10)
    for index, block in enumerate(blocks):
        if index == 0:
            doc.add_heading(block, 0)
        else:
            doc.add_paragraph(block)
    doc.save(source.with_suffix(".docx"))
    styles = getSampleStyleSheet()
    story = []
    for index, block in enumerate(blocks):
        story.append(Paragraph(block.replace("\n", "<br/>"), styles["Title" if index == 0 else "BodyText"]))
        story.append(Spacer(1, 10))
    SimpleDocTemplate(str(source.with_suffix(".pdf")), pagesize=A4).build(story)
    print("Demo PDF and DOCX generated.")


if __name__ == "__main__":
    main()
