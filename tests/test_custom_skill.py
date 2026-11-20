from engine import DEFAULT_SKILL, Model, parse, review
from app.schemas import SkillInput


def test_open_question_and_custom_skill_have_separate_grounded_rows():
    class FakeQwen(Model):
        def __init__(self):
            super().__init__(lambda *_: None, "tacc")

        def call(self, system, payload, phase):
            if phase == "survey":
                assert "发票" in payload["categories"]["custom_1"]
                return {"custom_1": ["P0001"], "custom_question": ["P0002"]}
            query = payload["requested"][0]
            rows = {
                "custom_1": ("P0001", "Invoices are due in 45 days.", "付款期限为 45 天。"),
                "custom_question": ("P0002", "Confidentiality survives for three years.", "保密义务持续三年。"),
            }
            paragraph_id, quote, finding = rows[query["category"]]
            return {"rows": [{"category": query["category"], "status": "found", "finding": finding,
                              "evidence": [{"paragraph_id": paragraph_id, "quote": quote}]}]}

    skill = SkillInput(name="付款专项", categories=[], custom_items=[
        {"title": "付款期限", "question": "收到发票后多久付款？"}]).model_dump()
    sample = b"Invoices are due in 45 days.\n\nConfidentiality survives for three years."
    result = review(parse("x.txt", sample), {**skill, "explicit": True}, "保密义务多久？", FakeQwen())
    assert [r["label"] for r in result["rows"]] == ["付款期限", "本次问题"]
    assert "保密义务持续三年" in result["answer"]
    assert len(result["rows"][1]["evidence"]) == 1


def test_demo_open_question_reports_model_requirement():
    result = review(parse("x.txt", b"Invoices are due in 45 days."), DEFAULT_SKILL,
                    "发票的付款期限是多少？", Model(lambda *_: None, "demo"))
    assert len(result["rows"]) == 1
    assert result["rows"][0]["category"] == "custom_question"
    assert "需要连接 TACC" in result["rows"][0]["explanation"]
