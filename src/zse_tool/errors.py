class ZseToolError(RuntimeError):
    """Base application error."""


class AccessBlocked(ZseToolError):
    """Remote endpoint appears to reject or challenge automated access."""


class RemoteDataError(ZseToolError):
    """Remote response is invalid or unexpected."""


class UnsupportedReport(ZseToolError):
    """Report format is not supported by this version."""
