from decimal import Decimal, InvalidOperation

from rest_framework import serializers


class FiniteCoordinateDecimalField(serializers.DecimalField):
    def to_internal_value(self, data):
        if isinstance(data, bool):
            raise serializers.ValidationError("A valid number is required.")
        try:
            value = Decimal(str(data))
        except (InvalidOperation, TypeError, ValueError):
            raise serializers.ValidationError(
                "A valid number is required."
            ) from None
        if not value.is_finite():
            raise serializers.ValidationError("A finite number is required.")
        if self.min_value is not None and value < Decimal(
            str(self.min_value)
        ):
            raise serializers.ValidationError(
                f"Ensure this value is greater than or equal to "
                f"{self.min_value}."
            )
        if self.max_value is not None and value > Decimal(
            str(self.max_value)
        ):
            raise serializers.ValidationError(
                f"Ensure this value is less than or equal to "
                f"{self.max_value}."
            )
        return super().to_internal_value(data)


class CoordinateQueryField(serializers.Field):
    default_error_messages = {
        "invalid": "A finite number within the valid geographic range is required.",
    }

    def __init__(self, *, minimum, maximum, **kwargs):
        self.minimum = Decimal(str(minimum))
        self.maximum = Decimal(str(maximum))
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        if isinstance(data, bool):
            self.fail("invalid")
        try:
            value = Decimal(str(data))
        except (InvalidOperation, TypeError, ValueError):
            self.fail("invalid")
        if (
            not value.is_finite()
            or not self.minimum <= value <= self.maximum
        ):
            self.fail("invalid")
        return value
