"""确定性交叉校验：用规则复核模型给出的可复现 checklist。

这一层不调用 LLM，因此便宜、稳定、可解释。它的作用不是替模型打分，而是
拦住「模型说有、原文里根本没有」这类幻觉。契约 §13 的四态枚举正好够用：
拿不准时降为 unable_to_verify，而不是断言论文没有提供。
"""
from __future__ import annotations

import re

from app.schemas.enums import ChecklistStatus
from app.schemas.report import Report, ReproducibilityCheck
from app.validation.errors import Severity, ValidationIssue

_REPO_HOSTS = (
    r"github\.com|gitlab\.com|bitbucket\.org|zenodo\.org|"
    r"huggingface\.co|osf\.io|codeocean\.com"
)
REPO_RE = re.compile(rf"https?://(?:www\.)?(?:{_REPO_HOSTS})/\S+", re.IGNORECASE)

# 存在性信号用宽松版：只要出现仓库域名就算论文提到了代码。
# 真实 PDF 抽出来常是 "https://github.com/ tianyu139/tangent-model-composition"
# ——换行被还原成空格，严格 URL 正则一个都匹配不到，于是把本来 available 的
# code_availability 误降成 unable_to_verify。降级只能因为「确实没有」，
# 不能因为解析器把 URL 拆断了。
REPO_SIGNAL_RE = re.compile(rf"\b(?:{_REPO_HOSTS})\b", re.IGNORECASE)

# 修复紧跟在斜杠后的断行空白，让 extract_repo_urls 还能拿回完整地址。
_SPLIT_URL_RE = re.compile(r"((?:https?://)\S+/)\s+(?=\S)")


def heal_split_urls(text: str) -> str:
    """把 "https://github.com/ user/repo" 接回 "https://github.com/user/repo"。"""
    previous = None
    while previous != text:
        previous, text = text, _SPLIT_URL_RE.sub(r"\1", text)
    return text
DATA_RE = re.compile(
    r"\b(dataset|datasets|benchmark|imagenet|cifar|coco|glue|wikitext|openml|"
    r"data\s+(?:is|are)\s+(?:publicly\s+)?available)\b",
    re.IGNORECASE,
)
SEED_RE = re.compile(r"\b(random\s+seed|seeds?\s*=|torch\.manual_seed|np\.random\.seed)\b", re.IGNORECASE)
HYPERPARAM_RE = re.compile(
    r"\b(learning\s+rate|batch\s+size|weight\s+decay|optimizer|adam|adamw|sgd|"
    r"epochs?|hyper-?parameters?)\b",
    re.IGNORECASE,
)
HARDWARE_RE = re.compile(
    r"\b(gpu|gpus|a100|h100|v100|rtx|tpu|cuda|nvidia|cpu\s+cores|hours?\s+of\s+training)\b",
    re.IGNORECASE,
)
PREPROCESS_RE = re.compile(
    r"\b(pre-?process|preprocessing|tokeniz|normaliz|augment|data\s+split|train/?val|"
    r"cleaning)\b",
    re.IGNORECASE,
)

_PATTERNS = {
    "code_availability": REPO_SIGNAL_RE,
    "data_availability": DATA_RE,
    "random_seed": SEED_RE,
    "hyperparameters": HYPERPARAM_RE,
    "hardware_environment": HARDWARE_RE,
    "preprocessing_details": PREPROCESS_RE,
}


def scan_source(source_text: str) -> dict[str, bool]:
    """扫描解析文本，返回每个 checklist 项是否存在对应信号。"""
    healed = heal_split_urls(source_text)
    return {key: bool(pattern.search(healed)) for key, pattern in _PATTERNS.items()}


def extract_repo_urls(source_text: str) -> list[str]:
    """抽出论文中的代码/数据仓库链接。

    MVP 只做存在性检查，不发 HTTP 探活（契约 §9 明确不做 GitHub 健康度检查）。
    """
    seen: list[str] = []
    for match in REPO_RE.findall(heal_split_urls(source_text)):
        url = match.rstrip(".,);]")
        if url not in seen:
            seen.append(url)
    return seen


def crosscheck_reproducibility(
    report: Report,
    source_text: str | None,
    code_links: list[str] | None = None,
) -> list[ValidationIssue]:
    """模型声称 available、但原文没有任何对应信号时，降为 unable_to_verify。

    只往「更保守」的方向改：不会把 not_available 提升为 available。

    `code_links` 是解析层已经抽出来的仓库链接（结构化规范 §8 的 link_type=code）。
    有这份结构化结果时它比正则更可靠，用来避免「解析器明明抽到了仓库地址、
    正则却没扫到」造成的误降级。
    """
    if not source_text:
        return []

    signals = scan_source(source_text)
    if code_links:
        signals["code_availability"] = True
    issues: list[ValidationIssue] = []
    new_checks: list[ReproducibilityCheck] = []

    for i, check in enumerate(report.reproducibility_checklist):
        if check.status is ChecklistStatus.AVAILABLE and not signals.get(check.check_id, True):
            data = check.model_dump()
            data["status"] = ChecklistStatus.UNABLE_TO_VERIFY
            data["detail"] = (
                f"{check.detail} [Automatic check: no matching signal in the parsed text; "
                f"status lowered to unable_to_verify]"
            )
            new_checks.append(ReproducibilityCheck(**data))
            issues.append(
                ValidationIssue(
                    code="CHECKLIST_DOWNGRADED",
                    path=f"reproducibility_checklist[{i}]",
                    message=(
                        f"'{check.check_id}' was marked available by the model, but the parsed text "
                        f"has no matching signal; lowered to unable_to_verify"
                    ),
                    severity=Severity.REPAIRED,
                )
            )
        else:
            new_checks.append(check)

    report.reproducibility_checklist = new_checks
    return issues
