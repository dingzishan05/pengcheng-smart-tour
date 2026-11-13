from enum import Enum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorCode(int, Enum):
    PARAM_INVALID = 40001
    SESSION_INVALID = 40101
    ADMIN_UNAUTHORIZED = 40102
    FORBIDDEN = 40301
    NOT_FOUND = 40401
    STATE_CONFLICT = 40901
    DATA_CONFLICT = 40902
    NO_FEASIBLE_PLAN = 50001
    OPTIMIZE_TIMEOUT = 50002
    LLM_UNAVAILABLE = 50003
    EXTERNAL_DATA_UNAVAILABLE = 50004
    INTERNAL_ERROR = 50005


class BizError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        detail: dict | None = None,
        session_id: str | None = None,
        plan_id: str | None = None,
        http_status: int = 200,
    ) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        self.session_id = session_id
        self.plan_id = plan_id
        self.http_status = http_status
        super().__init__(message)

    def to_payload(self) -> dict:
        payload: dict = {"code": int(self.code), "message": self.message}
        if self.detail is not None:
            payload["detail"] = self.detail
        if self.session_id is not None:
            payload["session_id"] = self.session_id
        if self.plan_id is not None:
            payload["plan_id"] = self.plan_id
        return payload


_DEFAULT_MESSAGES = {
    ErrorCode.PARAM_INVALID: "参数校验失败",
    ErrorCode.SESSION_INVALID: "游客会话无效",
    ErrorCode.ADMIN_UNAUTHORIZED: "管理员未登录或凭证失效",
    ErrorCode.FORBIDDEN: "无权限",
    ErrorCode.NOT_FOUND: "资源不存在",
    ErrorCode.STATE_CONFLICT: "状态冲突",
    ErrorCode.DATA_CONFLICT: "数据冲突",
    ErrorCode.NO_FEASIBLE_PLAN: "当前约束下无可行行程",
    ErrorCode.OPTIMIZE_TIMEOUT: "优化求解超时，已返回当前最优结果",
    ErrorCode.LLM_UNAVAILABLE: "AI 服务暂时不可用，请稍后重试",
    ErrorCode.EXTERNAL_DATA_UNAVAILABLE: "外部数据服务暂不可用",
    ErrorCode.INTERNAL_ERROR: "系统内部错误，请稍后重试",
}

_HTTP_STATUS_BY_CODE = {
    ErrorCode.PARAM_INVALID: 422,
    ErrorCode.SESSION_INVALID: 401,
    ErrorCode.ADMIN_UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.STATE_CONFLICT: 409,
    ErrorCode.DATA_CONFLICT: 409,
    ErrorCode.NO_FEASIBLE_PLAN: 422,
    ErrorCode.OPTIMIZE_TIMEOUT: 200,
    ErrorCode.LLM_UNAVAILABLE: 200,
    ErrorCode.EXTERNAL_DATA_UNAVAILABLE: 200,
    ErrorCode.INTERNAL_ERROR: 500,
}


def error_response(code: ErrorCode, message: str | None = None, **extra) -> JSONResponse:
    payload: dict = {"code": int(code), "message": message or _DEFAULT_MESSAGES[code]}
    payload.update({k: v for k, v in extra.items() if v is not None})
    return JSONResponse(status_code=_HTTP_STATUS_BY_CODE.get(code, 500), content=payload)


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizError)
    async def _biz_error_handler(_request: Request, exc: BizError) -> JSONResponse:
        status = _HTTP_STATUS_BY_CODE.get(exc.code, exc.http_status)
        return JSONResponse(status_code=status, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(ErrorCode.PARAM_INVALID, detail={"errors": exc.errors()})

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.PARAM_INVALID
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": int(code), "message": exc.detail if isinstance(exc.detail, str) else _DEFAULT_MESSAGES[code]},
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_request: Request, _exc: Exception) -> JSONResponse:
        return error_response(ErrorCode.INTERNAL_ERROR)