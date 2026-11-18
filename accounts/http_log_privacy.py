import logging
import re


class _RedactHttpQueryStrings(logging.Filter):
    """Remove URL query strings from urllib3 diagnostics before emission."""

    _query_pattern = re.compile(
        r"(?P<target>(?:https?://[^\s?\"']+|/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+))"
        r"\?[^\s\"']*"
    )

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        record.msg = self._query_pattern.sub(r"\g<target>?[REDACTED]", message)
        record.args = ()
        return True


_http_query_redactor = _RedactHttpQueryStrings()
for _http_logger_name in (
    "urllib3.connectionpool",
    "requests.packages.urllib3.connectionpool",
):
    _http_logger = logging.getLogger(_http_logger_name)
    if not any(isinstance(item, _RedactHttpQueryStrings) for item in _http_logger.filters):
        _http_logger.addFilter(_http_query_redactor)
