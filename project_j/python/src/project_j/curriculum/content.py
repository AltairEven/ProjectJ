from project_j.contracts import Document
from project_j.learning.contracts import DemandBody
from project_j.platform.files import PrivateFiles
from project_j.preparation.contracts import CourseBody, demand_requirements


def check_content(
    course: CourseBody, demand: DemandBody, material: Document, files: PrivateFiles
) -> dict:
    text = files.read_asset(material.body["asset"])
    mapped = {(item.field, item.requirement) for item in course.demand_mapping}
    missing = [item for item in demand_requirements(demand) if item not in mapped]
    if missing:
        raise ValueError(f"course does not implement or reuse demand items: {missing}")
    checks = [
        {"name": "fixed-material", "result": "pass", "evidence": material.body["asset"]["sha256"]},
        {
            "name": "budget-goals-references",
            "result": "pass",
            "evidence": "validated CourseBody and exact demand references",
        },
        {
            "name": "demand-mapping",
            "result": "pass",
            "evidence": f"{len(mapped)} demand items mapped to tasks or explicit reuse",
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
