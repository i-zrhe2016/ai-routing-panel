"""Small limiter command adapter using the existing node transport."""

import json
import os
import shlex

from .account_limits import manifest_hash


def reconcile_limits(node, manifest):
    command = shlex.split(os.environ.get("DATAPLANE_ACCOUNT_LIMITS_COMMAND", ""))
    if not command:
        raise RuntimeError("DATAPLANE_ACCOUNT_LIMITS_COMMAND 未配置，账号限速待应用。")
    if not node.is_configured():
        raise RuntimeError("数据面未纳管，无法应用账号限速。")
    runner = node.run_remote if node.is_remote else node.run_subprocess
    completed = runner(command, "账号限速应用失败", timeout=45, input_text=json.dumps(manifest))
    try:
        receipt = json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("账号限速节点未返回有效回执。") from exc
    if (
        not isinstance(receipt, dict)
        or receipt.get("desiredHash") != manifest_hash(manifest)
        or not receipt.get("kernelApplied")
        or not receipt.get("bootId")
        or not receipt.get("checkedAt")
    ):
        raise RuntimeError("账号限速回执与当前策略不一致。")
    receipt["policy"] = manifest
    return receipt
