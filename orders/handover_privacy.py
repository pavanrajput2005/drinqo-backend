"""Route-local Django error-report safeguards, not an APM/proxy logging filter."""
from django.views.debug import SafeExceptionReporterFilter


class HandoverExceptionReporterFilter(SafeExceptionReporterFilter):
    def is_active(self, request):
        # Suppress secret-bearing locals even on development error pages.
        return True

    def get_traceback_frame_variables(self, request, tb_frame):
        return [(name, self.cleansed_substitute) for name in tb_frame.f_locals]

    def get_post_parameters(self, request):
        return {"body": self.cleansed_substitute}


def protect_handover_request(request):
    request.exception_reporter_filter = HandoverExceptionReporterFilter()
    # Explicit integration hints; external collectors MUST honor these or exclude routes.
    request.sensitive_body = True
    request.sensitive_response = True
    request.sensitive_post_parameters = "__ALL__"
