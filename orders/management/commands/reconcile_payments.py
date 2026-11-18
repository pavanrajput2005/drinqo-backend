"""Disabled-by-default payment reconciliation entry point.

Running without --execute is intentionally read-only. Operational execution
requires the separately approved isolated/production runbook and provider
eligibility; it never changes gateway configuration.
"""
from django.core.management.base import BaseCommand, CommandError

from orders.models import OrderPayment
from orders.payments import reconcile_operational_payment


class Command(BaseCommand):
    help = "List or reconcile payment records requiring operational attention."

    def add_arguments(self, parser):
        parser.add_argument("--execute", action="store_true", help="Perform reconciliation; disabled by default.")
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1 or limit > 1000:
            raise CommandError("--limit must be between 1 and 1000.")
        queryset = OrderPayment.objects.filter(status__in=["refund_required", "refunding", "review"]).order_by("created_at")[:limit]
        if not options["execute"]:
            self.stdout.write(f"Reconciliation disabled: {queryset.count()} payment(s) require review.")
            return
        for payment in queryset:
            reconcile_operational_payment(payment)
        self.stdout.write("Reconciliation attempted; inspect operational cases before declaring outcomes.")
