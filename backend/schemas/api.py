"""API 请求/响应数据模型。"""

from typing import Optional, List

from pydantic import BaseModel, Field


# ═══ 请求模型 ═══


class OptimizeRequest(BaseModel):
    """简历优化请求体。"""

    resume_text: str = Field(..., min_length=10, description="原始简历纯文本")
    jd_text: str = Field(..., min_length=10, description="目标岗位 JD 纯文本")


class AuthRequest(BaseModel):
    """邮箱密码认证请求。"""

    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)


class AdminCreditAdjustmentRequest(BaseModel):
    """管理员额度调整请求。"""

    delta: int = Field(..., description="正数表示加额度，负数表示扣额度")
    reason: str = Field(..., min_length=2, max_length=200)


# ═══ 响应模型 ═══


class TaskCreatedResponse(BaseModel):
    """任务创建成功的响应。"""

    task_id: str
    status: str = "processing"
    message: str = "任务已提交，正在处理中"


class NodeLog(BaseModel):
    """单个节点的执行日志。"""

    node: str
    status: str  # "pending" | "running" | "done" | "error"
    message: Optional[str] = None
    duration_ms: Optional[int] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class TaskStatusResponse(BaseModel):
    """任务状态查询的响应。"""

    task_id: str
    status: str  # "processing" | "completed" | "failed"
    result: Optional[dict] = None
    error: Optional[str] = None
    node_logs: List[NodeLog] = []
    created_at: float
    completed_at: Optional[float] = None


class DashboardTaskHistoryItem(BaseModel):
    """Dashboard 历史任务列表项。"""

    task_id: str
    status: str
    original_file: Optional[str] = None
    created_at: float
    completed_at: Optional[float] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    total_tokens: int = 0
    llm_cost_usd: float = 0.0


class DashboardSummaryResponse(BaseModel):
    """Dashboard 顶部摘要。"""

    total_tasks: int = 0
    completed_tasks: int = 0
    processing_tasks: int = 0
    failed_tasks: int = 0
    total_tokens: int = 0
    total_llm_cost_usd: float = 0.0


class DashboardCreditChartPoint(BaseModel):
    """Dashboard 消耗趋势图上的单个数据点。"""

    date: str
    balance: int = 0
    delta: int = 0
    reason: str = ""


class DashboardCreditChartResponse(BaseModel):
    """Dashboard 消耗趋势图数据。"""

    metric_basis: str = "balance_history"
    current_credits: int = 0
    tier: str = "free"
    series: List[DashboardCreditChartPoint] = []


class DashboardProfileSummaryResponse(BaseModel):
    """Dashboard 画像摘要。"""

    profile_ready: bool = False
    email: str
    tier: str
    credits_balance: int = 0
    auth_provider: str
    is_admin: bool = False
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    processing_tasks: int = 0
    total_tokens: int = 0
    total_llm_cost_usd: float = 0.0
    experience_count: int = 0
    education_count: int = 0
    top_skill_categories: List[str] = []
    last_updated: Optional[float] = None
    last_completed_at: Optional[float] = None


class DashboardResponse(BaseModel):
    """Dashboard 工作台响应。"""

    summary: DashboardSummaryResponse
    recent_tasks: List[DashboardTaskHistoryItem]
    credit_chart: DashboardCreditChartResponse
    profile_summary: DashboardProfileSummaryResponse


class ErrorResponse(BaseModel):
    """通用错误响应。"""

    error: str
    detail: Optional[str] = None


class CurrentUserResponse(BaseModel):
    """当前登录用户信息。"""

    id: str
    email: str
    auth_provider: str
    credits: int
    tier: str
    is_admin: bool


class TokenResponse(BaseModel):
    """登录 / 注册成功后的令牌响应。"""

    access_token: str
    token_type: str = "bearer"
    user: CurrentUserResponse


class AdminUserListItem(BaseModel):
    """管理员视角下的用户列表项。"""

    id: str
    email: str
    auth_provider: str
    credits: int
    tier: str
    created_at: float
    is_admin: bool


class AdminUserListResponse(BaseModel):
    """管理员用户列表响应。"""

    items: List[AdminUserListItem]
    total: int
    page: int
    page_size: int


class AdminTaskListItem(BaseModel):
    """管理员视角下的任务列表项。"""

    task_id: str
    user_id: str
    user_email: str
    status: str
    original_file: Optional[str] = None
    created_at: float
    completed_at: Optional[float] = None
    error: Optional[str] = None


class AdminTaskListResponse(BaseModel):
    """管理员任务列表响应。"""

    items: List[AdminTaskListItem]
    total: int
    page: int
    page_size: int


class AdminStatsResponse(BaseModel):
    """管理员聚合统计响应。"""

    total_users: int
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    active_users_7d: int
    llm_cost_usd: float
