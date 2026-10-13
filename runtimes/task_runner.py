import json

from onchain.security import Denied, digest
from onchain.tasks import task_plan


async def run_template(task, claims, call, progress):
    if digest(task) != claims.get("task_hash"):
        raise Denied("TASK_CHANGED", "Task does not match the signed invocation.")
    template, plan = task_plan(task["template_id"], task["parameters"], task["template_digest"])
    await progress(kind="TASK_STARTED", source="task", label="开始任务：" + template["name"],
                   details={"template_id": template["id"], "version": template["version"], "steps": len(plan)})
    results = []
    failure = None
    operation = None
    for item in plan:
        value = await call(item["tool"], item["arguments"])
        if value.get("error"):
            failure = value["error"]
        if item["tool"] == "prepare_trade" and not failure:
            # Preserve the authoritative operation separately from bounded display previews.
            operation = value.get("operation")
        encoded = json.dumps(value, ensure_ascii=False)
        budget = 6000 // len(plan)
        visible = value if len(encoded.encode()) <= budget else {
            **{key: value[key] for key in ("provider", "source", "data_mode", "simulated", "notice") if key in value},
            "preview": encoded.encode()[:budget-100].decode(errors="ignore"), "truncated": True,
        }
        results.append({"tool": item["tool"], "status": "BLOCKED" if failure else "OK", "result": visible})
        if failure:
            break
    output = {"task_id": template["id"], "task_name": template["name"], "steps": results}
    if failure:
        output["error"] = failure
    elif operation:
        output.update(operation=operation, status="approval_required")
    return {"action": "task", "tool_result": output}
