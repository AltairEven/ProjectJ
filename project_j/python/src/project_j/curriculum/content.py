from project_j.contracts import Document
from project_j.learning.contracts import DemandBody
from project_j.platform.files import PrivateFiles
from project_j.preparation.contracts import CourseBody, demand_requirements
from project_j.preparation.reading import flat
from project_j.preparation.task_types import CONTENT_TYPES

QUOTED = {"answer"} | CONTENT_TYPES  # Task types whose evidence quotes the material.


def check_content(
    course: CourseBody, demand: DemandBody, material: Document, files: PrivateFiles
) -> dict:
    text = files.read_asset(material.body["asset"])
    mapped = {(item.field, item.requirement) for item in course.demand_mapping}
    missing = [item for item in demand_requirements(demand) if item not in mapped]
    if missing:
        raise ValueError(f"course does not implement or reuse demand items: {missing}")
    checks = [
        {"name": "material", "result": "pass", "evidence": material.body["asset"]["sha256"]},
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
        if task.purpose in QUOTED and flat(task.evidence) not in flat(text):
            raise ValueError("task evidence is not present in the material")
        checks.append(
            {
                "name": task.task_id,
                "result": "pass",
                "evidence": task.evidence,
                "method": "source_comparison"
                if task.purpose in QUOTED
                else "reported_conditions_only",
            }
        )
    return {
        "status": "checked",
        "scope": "rule check of drafted course",
        "checks": checks,
        "unverified": [
            "actual_model_semantics",
            "content_quality",
            "voice",
            "child_suitability",
            "teaching_effect",
        ],
    }
