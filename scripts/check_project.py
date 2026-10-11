"""One local command for executable-course and provenance checks."""
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_j.course import load_course, ROOT, validate

course = load_course()
errors = validate(course)
for asset in course["provenance"]["assets"]:
    name = Path(asset["path"]).name
    path = ROOT / "courses/paper-bridge" / name
    if hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
        errors.append("导入素材摘要不一致：" + name)
if errors:
    print("\n".join(errors))
    raise SystemExit(1)
print("课程结构、转场、预算、题卡依据与原材料摘要通过；不代表语音或教学验收通过。")
