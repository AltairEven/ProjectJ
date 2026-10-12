from project_j.contracts import Document
from project_j.platform.files import PrivateFiles
from project_j.preparation.contracts import CourseBody


def check_content(course: CourseBody, material: Document, files: PrivateFiles) -> dict:
    text = files.read_asset(material.body["asset"])
    checks = [
        {"name": "fixed-material", "result": "pass", "evidence": material.body["asset"]["sha256"]},
        {
            "name": "budget-goals-references",
            "result": "pass",
            "evidence": "validated CourseBody and exact demand references",
        },
    ]
    for task in course.tasks:
        if task.purpose == "answer" and task.evidence not in text:
            raise ValueError("task evidence is not present in the fixed source")
        checks.append(
            {
                "name": task.task_id,
                "result": "pass",
                "evidence": task.evidence,
                "method": "fixed_source_comparison"
                if task.purpose == "answer"
                else "reported_conditions_only",
            }
        )
    return {
        "status": "checked",
        "scope": "fixed simulation content",
        "checks": checks,
        "unverified": ["actual_model_semantics", "voice", "child_suitability", "teaching_effect"],
    }
