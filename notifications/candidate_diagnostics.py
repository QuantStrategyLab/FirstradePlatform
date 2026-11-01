"""Closed diagnostic vocabulary: never include exception text or storage paths."""
from contextlib import contextmanager

from google.api_core import exceptions as gcs_errors
from google.auth.exceptions import GoogleAuthError
from requests.exceptions import RequestException, Timeout


STAGES = {
    "binding": ("绑定校验", "binding validation"),
    "storage_init": ("存储初始化", "storage initialization"),
    "archive_list": ("归档列表读取", "archive listing"),
    "archive_read": ("归档内容读取", "archive read"),
    "projection": ("候选投影", "candidate projection"),
    "candidate_read": ("候选历史读取", "candidate history read"),
    "candidate_merge": ("候选合并", "candidate merge"),
    "storage_serialize": ("存储内容序列化", "storage serialization"),
    "candidate_write": ("候选写入", "candidate write"),
    "unclassified": ("未知阶段", "unknown stage"),
}
REASONS = {
    "permission_denied": ("权限被拒绝", "permission denied"),
    "authentication": ("认证失败", "authentication failed"),
    "not_found": ("对象不存在", "object not found"),
    "timeout": ("请求超时，结果未确认", "request timed out; outcome unconfirmed"),
    "transport": ("传输失败，结果未确认", "transport failed; outcome unconfirmed"),
    "invalid_data": ("数据或配置无效", "invalid data or configuration"),
    "conflict": ("并发写入冲突", "concurrent write conflict"),
    "unknown": ("原因未确认", "cause unconfirmed"),
}


class CandidateOperationError(RuntimeError):
    def __init__(self, stage, reason):
        self.stage = stage if isinstance(stage, str) and stage in STAGES else "unclassified"
        self.reason = reason if isinstance(reason, str) and reason in REASONS else "unknown"
        super().__init__(f"{self.stage}:{self.reason}")


def classify(exc, stage):
    if isinstance(exc, CandidateOperationError):
        return CandidateOperationError(exc.stage, exc.reason)
    if isinstance(exc, gcs_errors.Forbidden):
        reason = "permission_denied"
    elif isinstance(exc, (gcs_errors.Unauthorized, GoogleAuthError)):
        reason = "authentication"
    elif isinstance(exc, gcs_errors.NotFound):
        reason = "not_found"
    elif isinstance(exc, (TimeoutError, Timeout, gcs_errors.DeadlineExceeded)):
        reason = "timeout"
    elif isinstance(exc, (RequestException, gcs_errors.ServiceUnavailable, ConnectionError)):
        reason = "transport"
    elif isinstance(exc, (gcs_errors.PreconditionFailed, gcs_errors.Conflict)):
        reason = "conflict"
    elif isinstance(exc, (ValueError, TypeError, KeyError)):
        reason = "invalid_data"
    else:
        reason = "unknown"
    return CandidateOperationError(stage, reason)


@contextmanager
def candidate_stage(stage):
    try:
        yield
    except CandidateOperationError:
        raise
    except Exception as exc:
        raise classify(exc, stage) from None


def failure_text(error, locale):
    diagnostic = classify(error, "unclassified")
    zh = str(locale or "").strip().lower().startswith("zh")
    stage = STAGES[diagnostic.stage][0 if zh else 1]
    reason = REASONS[diagnostic.reason][0 if zh else 1]
    if zh:
        return f"通知候选发布失败。\n阶段：{stage}。\n原因：{reason}。\n请查看本次运行记录。"
    return f"Notification candidate publication failed.\nStage: {stage}.\nReason: {reason}.\nReview the current run record."
